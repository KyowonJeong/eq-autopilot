// EdgeQuant — Author: Kyowon Jeong — Started: 2026-04-13
// =========================
// EQAutopilotBridge.cs — NinjaTrader 8 씬 애드온 (A안: 앱=두뇌 / NT8=팔)
//
// 역할은 단 세 가지:
//   1) 1초마다 EQ Autopilot 앱(127.0.0.1)의 브리지에서 대기 명령을 가져와 실행
//      (entry[+스탑 브래킷] / stop / close / flatten)
//   2) 실행 결과를 tid와 함께 ack (멱등 — 같은 tid는 두 번 실행 안 함)
//   3) 계정·포지션·잔고 스냅샷을 1초마다 push (앱의 fail-closed 하트비트)
//
// 전략 로직·사이징·판단은 전부 앱에 있다. 이 파일은 절대 스스로 판단하지 않는다.
//
// 설치: 문서\NinjaTrader 8\bin\Custom\AddOns\ 에 복사 → NinjaScript Editor에서 컴파일(F5).
// 설정: 아래 Port/Token을 앱 설정과 동일하게. 자세한 것은 README.md.
//
// ⚠️ 스캐폴드 상태(2026-07-30): 계정 API 호출부(Submit/Flatten)는 NT8 8.1 기준으로
// 작성했으며, 실계정 연결 후 데모에서 검증할 것. VERIFY 표시 참조.
// =========================
#region Using declarations
using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Threading;
using Newtonsoft.Json.Linq;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.AddOns
{
    public class EQAutopilotBridge : AddOnBase
    {
        // ── 설정(앱과 동일해야 함) ──
        private const string BaseUrl = "http://127.0.0.1:8377";
        private const string Token   = "CHANGE-ME-SHARED-TOKEN";   // 앱 config의 nt8.token
        private const int    PollMs  = 1000;

        private DispatcherTimer timer;
        private static readonly HttpClient http = new HttpClient();
        private readonly HashSet<string> doneTids = new HashSet<string>();
        private bool busy;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "EdgeQuant Autopilot bridge — executes app commands, pushes state.";
                Name = "EQAutopilotBridge";
            }
            else if (State == State.Configure)
            {
                http.DefaultRequestHeaders.Remove("X-EQ-Bridge-Token");
                http.DefaultRequestHeaders.Add("X-EQ-Bridge-Token", Token);
                http.Timeout = TimeSpan.FromSeconds(3);
            }
        }

        protected override void OnWindowCreated(System.Windows.Window window)
        {
            if (timer != null) return;                    // 창마다 중복 기동 방지
            timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(PollMs) };
            timer.Tick += async (s, e) => await TickAsync();
            timer.Start();
            Print("[EQBridge] started, polling " + BaseUrl);
        }

        protected override void OnWindowDestroyed(System.Windows.Window window)
        {
            // 마지막 창이 닫힐 때만 정지 — NT 종료 시 자동 정리
        }

        private async Task TickAsync()
        {
            if (busy) return;                             // 재진입 방지
            busy = true;
            try
            {
                await PushStateAsync();
                await DrainCommandsAsync();
            }
            catch (Exception ex)
            {
                Print("[EQBridge] tick error: " + ex.Message);   // 앱 꺼짐 = 조용히 대기
            }
            finally { busy = false; }
        }

        // ── ① 상태 push: 계정·포지션·잔고 + 하트비트 ──
        private async Task PushStateAsync()
        {
            var accounts = new JArray();
            var positions = new JArray();
            lock (Account.All)
            {
                foreach (Account a in Account.All)
                {
                    if (a.ConnectionStatus != ConnectionStatus.Connected) continue;
                    accounts.Add(new JObject {
                        ["name"] = a.Name,
                        ["cash_value"] = a.Get(AccountItem.CashValue, Currency.UsDollar),
                        ["realized_pnl"] = a.Get(AccountItem.RealizedProfitLoss, Currency.UsDollar),
                    });
                    foreach (var p in a.Positions)
                    {
                        int q = p.MarketPosition == MarketPosition.Long ? p.Quantity
                              : p.MarketPosition == MarketPosition.Short ? -p.Quantity : 0;
                        if (q == 0) continue;
                        positions.Add(new JObject {
                            ["account"] = a.Name,
                            ["instrument"] = p.Instrument.FullName,   // "MNQ 09-26"
                            ["net_qty"] = q,
                            ["avg_price"] = p.AveragePrice,
                        });
                    }
                }
            }
            var body = new JObject { ["accounts"] = accounts, ["positions"] = positions,
                                     ["ts"] = DateTimeOffset.UtcNow.ToUnixTimeSeconds() };
            await PostAsync("/v1/state", body);
        }

        // ── ② 명령 실행 ──
        private async Task DrainCommandsAsync()
        {
            var resp = await http.GetStringAsync(BaseUrl + "/v1/pending");
            var cmds = (JArray)(JObject.Parse(resp)["commands"] ?? new JArray());
            foreach (JObject c in cmds)
            {
                string tid = (string)c["tid"];
                if (string.IsNullOrEmpty(tid) || doneTids.Contains(tid)) continue;
                doneTids.Add(tid);
                JObject ack = new JObject { ["tid"] = tid, ["ok"] = false };
                try
                {
                    switch ((string)c["op"])
                    {
                        case "entry":   ack = ExecEntry(c);   break;
                        case "stop":    ack = ExecStop(c);    break;
                        case "close":   ack = ExecClose(c);   break;
                        case "flatten": ack = ExecFlatten(c); break;
                        default: ack["error"] = "unknown op"; break;
                    }
                }
                catch (Exception ex) { ack["error"] = ex.Message; }
                ack["tid"] = tid;
                await PostAsync("/v1/ack", ack);
            }
        }

        private static Account FindAccount(string name)
        {
            lock (Account.All)
                return Account.All.FirstOrDefault(a => a.Name == name
                        && a.ConnectionStatus == ConnectionStatus.Connected);
        }

        private static Instrument FindInstrument(string full)
        {
            return Instrument.GetInstrument(full);        // "MNQ 09-26" — VERIFY 시장가 즉시 반환
        }

        // entry: 시장가(정본=봉마감 최속) + stop_loss_price 있으면 브래킷.
        // 스탑 제출 실패 시 진입분 즉시 플래튼 — 알몸 포지션 불가 원칙.
        private JObject ExecEntry(JObject c)
        {
            var acct = FindAccount((string)c["account"]);
            if (acct == null) throw new Exception("account not connected: " + c["account"]);
            var instr = FindInstrument((string)c["instrument"]);
            if (instr == null) throw new Exception("instrument not found: " + c["instrument"]);
            int qty = (int)c["qty"];
            bool buy = (string)c["side"] == "buy";
            var action = buy ? OrderAction.Buy : OrderAction.SellShort;
            bool isLimit = (string)c["order_type"] == "limit" && c["limit_price"] != null;

            // 진입 주문 — VERIFY: NT 8.1 CreateOrder 시그니처(oco/strategy 인자)
            Order entry = acct.CreateOrder(instr, action,
                isLimit ? OrderType.Limit : OrderType.Market,
                OrderEntry.Automated, TimeInForce.Day, qty,
                isLimit ? (double)c["limit_price"] : 0, 0,
                "", "EQ-" + c["tid"], Core.Globals.MaxDate, null);
            acct.Submit(new[] { entry });

            var ack = new JObject { ["ok"] = true, ["order_id"] = entry.OrderId };
            if (c["stop_loss_price"] != null && c["stop_loss_price"].Type != JTokenType.Null)
            {
                try
                {
                    var stopAction = buy ? OrderAction.Sell : OrderAction.BuyToCover;
                    Order stop = acct.CreateOrder(instr, stopAction, OrderType.StopMarket,
                        OrderEntry.Automated, TimeInForce.Gtc, qty,
                        0, (double)c["stop_loss_price"],
                        "", "EQS-" + c["tid"], Core.Globals.MaxDate, null);
                    acct.Submit(new[] { stop });
                    ack["stop_order_id"] = stop.OrderId;
                }
                catch (Exception ex)
                {
                    acct.Flatten(new[] { instr });        // 스탑 실패 → 즉시 정리
                    throw new Exception("stop rejected, entry flattened: " + ex.Message);
                }
            }
            return ack;
        }

        private JObject ExecStop(JObject c)
        {
            var acct = FindAccount((string)c["account"]);
            var instr = FindInstrument((string)c["instrument"]);
            if (acct == null || instr == null) throw new Exception("account/instrument missing");
            bool sell = (string)c["side"] == "sell";
            Order stop = acct.CreateOrder(instr,
                sell ? OrderAction.Sell : OrderAction.BuyToCover, OrderType.StopMarket,
                OrderEntry.Automated, TimeInForce.Gtc, (int)c["qty"],
                0, (double)c["stop_price"], "", "EQS-" + c["tid"],
                Core.Globals.MaxDate, null);
            acct.Submit(new[] { stop });
            return new JObject { ["ok"] = true, ["order_id"] = stop.OrderId };
        }

        private JObject ExecClose(JObject c)
        {
            var acct = FindAccount((string)c["account"]);
            var instr = FindInstrument((string)c["instrument"]);
            if (acct == null || instr == null) throw new Exception("account/instrument missing");
            acct.Flatten(new[] { instr });                 // 해당 종목만 정리(주문 취소 포함)
            return new JObject { ["ok"] = true };
        }

        private JObject ExecFlatten(JObject c)
        {
            var wanted = (c["accounts"] as JArray)?.Select(t => (string)t).ToHashSet();
            lock (Account.All)
            {
                foreach (Account a in Account.All)
                {
                    if (a.ConnectionStatus != ConnectionStatus.Connected) continue;
                    if (wanted != null && !wanted.Contains(a.Name)) continue;
                    var instrs = a.Positions.Where(p => p.Quantity != 0)
                                            .Select(p => p.Instrument).Distinct().ToArray();
                    if (instrs.Length > 0) a.Flatten(instrs);
                }
            }
            return new JObject { ["ok"] = true };
        }

        private static async Task PostAsync(string path, JObject body)
        {
            var content = new StringContent(body.ToString(), Encoding.UTF8, "application/json");
            var r = await http.PostAsync(BaseUrl + path, content);
            r.EnsureSuccessStatusCode();
        }
    }
}
