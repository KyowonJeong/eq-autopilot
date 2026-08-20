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
// 의존성 제로(2026-08-11): Newtonsoft 참조가 NT8 표준 설치에 없어 컴파일이 깨지던 것
// (대표 실기기 CS0246)을 계기로, 아래 MiniJson(자체 파서·직렬화)으로 교체했다.
// References 추가 없이 F5 한 번으로 컴파일되는 것이 회원 배포의 전제다.
//
// ⚠️ 스캐폴드 상태: 계정 API 호출부(CreateOrder/Submit/Flatten)는 NT8 8.1 기준으로
// 작성했으며, 실계정 연결 후 데모에서 검증할 것. VERIFY 표시 참조.
// =========================
#region Using declarations
using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Threading;
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
            var accounts = new List<object>();
            var positions = new List<object>();
            lock (Account.All)
            {
                foreach (Account a in Account.All)
                {
                    if (a.ConnectionStatus != ConnectionStatus.Connected) continue;
                    accounts.Add(new Dictionary<string, object> {
                        { "name", a.Name },
                        { "cash_value", a.Get(AccountItem.CashValue, Currency.UsDollar) },
                        { "realized_pnl", a.Get(AccountItem.RealizedProfitLoss, Currency.UsDollar) },
                        // 통과 익절(2026-08-20): NetLiq = 잔고+미실현 - 앱이 시세·포인트가치
                        // 없이 (잔고+미실현)≥목표를 판정하는 유일한 원천. 구 앱은 이 필드를
                        // 모른 채 무시하므로 호환에 영향 없음.
                        { "net_liq", a.Get(AccountItem.NetLiquidation, Currency.UsDollar) },
                        { "unrealized_pnl", a.Get(AccountItem.UnrealizedProfitLoss, Currency.UsDollar) },
                    });
                    foreach (var p in a.Positions)
                    {
                        int q = p.MarketPosition == MarketPosition.Long ? p.Quantity
                              : p.MarketPosition == MarketPosition.Short ? -p.Quantity : 0;
                        if (q == 0) continue;
                        positions.Add(new Dictionary<string, object> {
                            { "account", a.Name },
                            { "instrument", p.Instrument.FullName },   // "MNQ 09-26"
                            { "net_qty", q },
                            { "avg_price", p.AveragePrice },
                        });
                    }
                }
            }
            // ── 체결 이력(2026-08-14): 공개 트랙레코드가 브로커 체결에서 생성되는데 NT8만
            //    조회 경로가 없어 루시드 거래가 기록에서 통째로 빠졌다. 상태 push에 얹는다
            //    (별도 엔드포인트 불필요 - 이미 주기적으로 도는 경로).
            //    NinjaTrader는 Account.Executions에 세션 체결을 들고 있다. 앱이 90일 창으로
            //    합산하므로 여기서는 있는 그대로 넘기고, 중복은 앱·서버 tid 멱등이 흡수한다.
            var executions = new List<object>();
            try
            {
                lock (Account.All)
                {
                    foreach (Account a in Account.All)
                    {
                        if (a.ConnectionStatus != ConnectionStatus.Connected) continue;
                        foreach (Execution ex in a.Executions)
                        {
                            if (ex == null || ex.Instrument == null) continue;
                            executions.Add(new Dictionary<string, object> {
                                { "account", a.Name },
                                { "exec_id", ex.ExecutionId },
                                { "instrument", ex.Instrument.FullName },
                                { "side", ex.MarketPosition == MarketPosition.Long ? "BUY" : "SELL" },
                                { "qty", ex.Quantity },
                                { "price", ex.Price },
                                { "time", ex.Time.ToUniversalTime()
                                            .ToString("yyyy-MM-ddTHH:mm:ssZ") },
                                { "commission", ex.Commission },
                                { "pnl", ex.Position != null ? ex.Position.GetUnrealizedProfitLoss(
                                            PerformanceUnit.Currency, ex.Price) : 0.0 },
                            });
                        }
                    }
                }
            }
            catch (Exception) { /* 체결 조회 실패는 상태 push 전체를 막지 않는다 */ }

            var body = new Dictionary<string, object> {
                { "accounts", accounts }, { "positions", positions },
                { "executions", executions },
                { "ts", DateTimeOffset.UtcNow.ToUnixTimeSeconds() },
            };
            await PostAsync("/v1/state", body);
        }

        // ── ② 명령 실행 ──
        private async Task DrainCommandsAsync()
        {
            var resp = await http.GetStringAsync(BaseUrl + "/v1/pending");
            var root = MiniJson.Parse(resp) as Dictionary<string, object>;
            var cmds = (root != null ? root.Get("commands") : null) as List<object>
                       ?? new List<object>();
            foreach (var co in cmds)
            {
                var c = co as Dictionary<string, object>;
                if (c == null) continue;
                string tid = c.Str("tid");
                if (string.IsNullOrEmpty(tid) || doneTids.Contains(tid)) continue;
                doneTids.Add(tid);
                Dictionary<string, object> ack;
                try
                {
                    switch (c.Str("op"))
                    {
                        case "entry":   ack = ExecEntry(c);   break;
                        case "stop":    ack = ExecStop(c);    break;
                        case "close":   ack = ExecClose(c);   break;
                        case "flatten": ack = ExecFlatten(c); break;
                        default: ack = Ack(false); ack["error"] = "unknown op"; break;
                    }
                }
                catch (Exception ex) { ack = Ack(false); ack["error"] = ex.Message; }
                ack["tid"] = tid;
                await PostAsync("/v1/ack", ack);
            }
        }

        private static Dictionary<string, object> Ack(bool ok)
        {
            return new Dictionary<string, object> { { "ok", ok } };
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
        private Dictionary<string, object> ExecEntry(Dictionary<string, object> c)
        {
            var acct = FindAccount(c.Str("account"));
            if (acct == null) throw new Exception("account not connected: " + c.Str("account"));
            var instr = FindInstrument(c.Str("instrument"));
            if (instr == null) throw new Exception("instrument not found: " + c.Str("instrument"));
            int qty = c.Int("qty");
            bool buy = c.Str("side") == "buy";
            var action = buy ? OrderAction.Buy : OrderAction.SellShort;
            bool isLimit = c.Str("order_type") == "limit" && c.Get("limit_price") != null;

            // 진입 주문 — VERIFY: NT 8.1 CreateOrder 시그니처(oco/strategy 인자)
            Order entry = acct.CreateOrder(instr, action,
                isLimit ? OrderType.Limit : OrderType.Market,
                OrderEntry.Automated, TimeInForce.Day, qty,
                isLimit ? c.Dbl("limit_price") : 0, 0,
                "", "EQ-" + c.Str("tid"), Core.Globals.MaxDate, null);
            acct.Submit(new[] { entry });

            var ack = Ack(true);
            ack["order_id"] = entry.OrderId;
            if (c.Get("stop_loss_price") != null)
            {
                try
                {
                    var stopAction = buy ? OrderAction.Sell : OrderAction.BuyToCover;
                    Order stop = acct.CreateOrder(instr, stopAction, OrderType.StopMarket,
                        OrderEntry.Automated, TimeInForce.Gtc, qty,
                        0, c.Dbl("stop_loss_price"),
                        "", "EQS-" + c.Str("tid"), Core.Globals.MaxDate, null);
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

        private Dictionary<string, object> ExecStop(Dictionary<string, object> c)
        {
            var acct = FindAccount(c.Str("account"));
            var instr = FindInstrument(c.Str("instrument"));
            if (acct == null || instr == null) throw new Exception("account/instrument missing");
            bool sell = c.Str("side") == "sell";
            Order stop = acct.CreateOrder(instr,
                sell ? OrderAction.Sell : OrderAction.BuyToCover, OrderType.StopMarket,
                OrderEntry.Automated, TimeInForce.Gtc, c.Int("qty"),
                0, c.Dbl("stop_price"), "", "EQS-" + c.Str("tid"),
                Core.Globals.MaxDate, null);
            acct.Submit(new[] { stop });
            var ack = Ack(true);
            ack["order_id"] = stop.OrderId;
            return ack;
        }

        private Dictionary<string, object> ExecClose(Dictionary<string, object> c)
        {
            var acct = FindAccount(c.Str("account"));
            var instr = FindInstrument(c.Str("instrument"));
            if (acct == null || instr == null) throw new Exception("account/instrument missing");
            acct.Flatten(new[] { instr });                 // 해당 종목만 정리(주문 취소 포함)
            return Ack(true);
        }

        private Dictionary<string, object> ExecFlatten(Dictionary<string, object> c)
        {
            HashSet<string> wanted = null;
            var arr = c.Get("accounts") as List<object>;
            if (arr != null)
                wanted = new HashSet<string>(arr.Select(t => t as string).Where(t => t != null));
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
            return Ack(true);
        }

        private static async Task PostAsync(string path, Dictionary<string, object> body)
        {
            var content = new StringContent(MiniJson.Serialize(body), Encoding.UTF8,
                                            "application/json");
            var r = await http.PostAsync(BaseUrl + path, content);
            r.EnsureSuccessStatusCode();
        }
    }

    // ── 사전 접근 헬퍼 ──
    internal static class DictExt
    {
        public static object Get(this Dictionary<string, object> d, string k)
        {
            object v;
            return d != null && d.TryGetValue(k, out v) ? v : null;
        }
        public static string Str(this Dictionary<string, object> d, string k)
        {
            var v = d.Get(k);
            return v == null ? null : v.ToString();
        }
        public static int Int(this Dictionary<string, object> d, string k)
        {
            return (int)Convert.ToDouble(d.Get(k) ?? 0, CultureInfo.InvariantCulture);
        }
        public static double Dbl(this Dictionary<string, object> d, string k)
        {
            return Convert.ToDouble(d.Get(k) ?? 0, CultureInfo.InvariantCulture);
        }
    }

    // ── MiniJson — 의존성 제로 JSON (파서 + 직렬화) ─────────────────────────────
    // 브리지 프로토콜에 필요한 만큼만: object/array/string/number/bool/null.
    // 값 타입: Dictionary<string,object> / List<object> / string / double / bool / null.
    internal static class MiniJson
    {
        public static object Parse(string s)
        {
            int i = 0;
            var v = ParseValue(s, ref i);
            return v;
        }

        private static object ParseValue(string s, ref int i)
        {
            SkipWs(s, ref i);
            if (i >= s.Length) throw new Exception("json: eof");
            char ch = s[i];
            if (ch == '{') return ParseObj(s, ref i);
            if (ch == '[') return ParseArr(s, ref i);
            if (ch == '"') return ParseStr(s, ref i);
            if (ch == 't') { Expect(s, ref i, "true"); return true; }
            if (ch == 'f') { Expect(s, ref i, "false"); return false; }
            if (ch == 'n') { Expect(s, ref i, "null"); return null; }
            return ParseNum(s, ref i);
        }

        private static Dictionary<string, object> ParseObj(string s, ref int i)
        {
            var d = new Dictionary<string, object>();
            i++;                                          // '{'
            SkipWs(s, ref i);
            if (s[i] == '}') { i++; return d; }
            while (true)
            {
                SkipWs(s, ref i);
                string k = ParseStr(s, ref i);
                SkipWs(s, ref i);
                if (s[i] != ':') throw new Exception("json: ':' expected");
                i++;
                d[k] = ParseValue(s, ref i);
                SkipWs(s, ref i);
                if (s[i] == ',') { i++; continue; }
                if (s[i] == '}') { i++; return d; }
                throw new Exception("json: ',' or '}' expected");
            }
        }

        private static List<object> ParseArr(string s, ref int i)
        {
            var a = new List<object>();
            i++;                                          // '['
            SkipWs(s, ref i);
            if (s[i] == ']') { i++; return a; }
            while (true)
            {
                a.Add(ParseValue(s, ref i));
                SkipWs(s, ref i);
                if (s[i] == ',') { i++; continue; }
                if (s[i] == ']') { i++; return a; }
                throw new Exception("json: ',' or ']' expected");
            }
        }

        private static string ParseStr(string s, ref int i)
        {
            if (s[i] != '"') throw new Exception("json: '\"' expected");
            var sb = new StringBuilder();
            i++;
            while (true)
            {
                char ch = s[i++];
                if (ch == '"') return sb.ToString();
                if (ch == '\\')
                {
                    char e = s[i++];
                    switch (e)
                    {
                        case '"': sb.Append('"'); break;
                        case '\\': sb.Append('\\'); break;
                        case '/': sb.Append('/'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'u':
                            sb.Append((char)Convert.ToInt32(s.Substring(i, 4), 16));
                            i += 4; break;
                        default: throw new Exception("json: bad escape");
                    }
                }
                else sb.Append(ch);
            }
        }

        private static double ParseNum(string s, ref int i)
        {
            int start = i;
            while (i < s.Length && (char.IsDigit(s[i]) || s[i] == '-' || s[i] == '+'
                                    || s[i] == '.' || s[i] == 'e' || s[i] == 'E'))
                i++;
            return double.Parse(s.Substring(start, i - start), CultureInfo.InvariantCulture);
        }

        private static void SkipWs(string s, ref int i)
        {
            while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
        }

        private static void Expect(string s, ref int i, string word)
        {
            if (string.CompareOrdinal(s, i, word, 0, word.Length) != 0)
                throw new Exception("json: '" + word + "' expected");
            i += word.Length;
        }

        public static string Serialize(object v)
        {
            var sb = new StringBuilder();
            Write(sb, v);
            return sb.ToString();
        }

        private static void Write(StringBuilder sb, object v)
        {
            if (v == null) { sb.Append("null"); return; }
            if (v is bool) { sb.Append((bool)v ? "true" : "false"); return; }
            if (v is string) { WriteStr(sb, (string)v); return; }
            if (v is Dictionary<string, object>)
            {
                sb.Append('{');
                bool first = true;
                foreach (var kv in (Dictionary<string, object>)v)
                {
                    if (!first) sb.Append(',');
                    first = false;
                    WriteStr(sb, kv.Key);
                    sb.Append(':');
                    Write(sb, kv.Value);
                }
                sb.Append('}');
                return;
            }
            if (v is IEnumerable && !(v is string))
            {
                sb.Append('[');
                bool first = true;
                foreach (var it in (IEnumerable)v)
                {
                    if (!first) sb.Append(',');
                    first = false;
                    Write(sb, it);
                }
                sb.Append(']');
                return;
            }
            // 숫자(int/long/double/decimal 등)
            sb.Append(Convert.ToString(v, CultureInfo.InvariantCulture));
        }

        private static void WriteStr(StringBuilder sb, string s)
        {
            sb.Append('"');
            foreach (char ch in s)
            {
                switch (ch)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (ch < ' ') sb.Append("\\u").Append(((int)ch).ToString("x4"));
                        else sb.Append(ch);
                        break;
                }
            }
            sb.Append('"');
        }
    }
}
