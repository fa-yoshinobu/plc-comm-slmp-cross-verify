using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using PlcComm.Slmp;

namespace SlmpVerifyClient
{
    class Program
    {
        static async Task Main(string[] args)
        {
            if (args.Length < 3) return;
            string host = args[0], port = args[1], command = args[2];
            string address = args.Length > 3 ? args[3] : "";
            var frame = SlmpFrameType.Frame3E;
            var series = SlmpCompatibilityMode.Legacy;
            SlmpTargetAddress? target = null;
            string mode = "word";
            string wordDevs = "", dwordDevs = "", wordsKv = "", dwordsKv = "", bitsKv = "";
            string wordBlocks = "", bitBlocks = "";
            var cmd_args = new List<string>();

            for (int i = 4; i < args.Length; i++)
            {
                if (args[i] == "--frame") frame = args[++i] == "4e" ? SlmpFrameType.Frame4E : SlmpFrameType.Frame3E;
                else if (args[i] == "--series") series = args[++i] == "iqr" ? SlmpCompatibilityMode.Iqr : SlmpCompatibilityMode.Legacy;
                else if (args[i] == "--mode") mode = args[++i];
                else if (args[i] == "--target")
                {
                    var p = args[++i].Split(',');
                    target = new SlmpTargetAddress(byte.Parse(p[0]), byte.Parse(p[1]), ushort.Parse(p[2]), byte.Parse(p[3]));
                }
                else if (args[i] == "--word-devs") wordDevs = args[++i];
                else if (args[i] == "--dword-devs") dwordDevs = args[++i];
                else if (args[i] == "--words") wordsKv = args[++i];
                else if (args[i] == "--dwords") dwordsKv = args[++i];
                else if (args[i] == "--bits") bitsKv = args[++i];
                else if (args[i] == "--word-blocks") wordBlocks = args[++i];
                else if (args[i] == "--bit-blocks") bitBlocks = args[++i];
                else cmd_args.Add(args[i]);
            }

            using var client = new SlmpClient(host, int.Parse(port)) { FrameType = frame, CompatibilityMode = series };
            if (target.HasValue) client.TargetAddress = target.Value;
            object? result = null;
            try
            {
                await client.OpenAsync();

                // --- Basic device read/write ---
                if (command == "read")
                {
                    var dev = SlmpDeviceParser.Parse(address);
                    int count = cmd_args.Count > 0 ? int.Parse(cmd_args[0]) : 1;
                    if (mode == "bit") result = new { status = "success", values = (await client.ReadBitsAsync(dev, (ushort)count)).Select(v => v ? 1 : 0) };
                    else if (mode == "dword") result = new { status = "success", values = await client.ReadDWordsRawAsync(dev, (ushort)count) };
                    else if (mode == "float") result = new { status = "success", values = await client.ReadFloat32sAsync(dev, (ushort)count) };
                    else result = new { status = "success", values = await client.ReadWordsRawAsync(dev, (ushort)count) };
                }
                else if (command == "write")
                {
                    var dev = SlmpDeviceParser.Parse(address);
                    if (mode == "bit") await client.WriteBitsAsync(dev, cmd_args.Select(v => v == "1").ToArray());
                    else if (mode == "dword") await client.WriteDWordsAsync(dev, cmd_args.Select(uint.Parse).ToArray());
                    else if (mode == "float") await client.WriteFloat32sAsync(dev, cmd_args.Select(float.Parse).ToArray());
                    else await client.WriteWordsAsync(dev, cmd_args.Select(ushort.Parse).ToArray());
                    result = new { status = "success" };
                }

                // --- Type name ---
                else if (command == "read-type")
                {
                    var info = await client.ReadTypeNameAsync();
                    result = new { status = "success", model = info.Model, model_code = "0x" + info.ModelCode.ToString("X4") };
                }
                else if (command == "read-named")
                {
                    var addresses = ParseNamedAddresses(address);
                    var values = await client.ReadNamedAsync(addresses);
                    result = new
                    {
                        status = "success",
                        addresses,
                        values = addresses.Select(key => values[key]).ToArray(),
                    };
                }
                else if (command == "write-named")
                {
                    var updates = ParseNamedUpdates(address);
                    await client.WriteNamedAsync(updates);
                    result = new { status = "success" };
                }
                else if (command == "poll-once")
                {
                    var addresses = ParseNamedAddresses(address);
                    await foreach (var snapshot in client.PollAsync(addresses, TimeSpan.Zero))
                    {
                        result = new
                        {
                            status = "success",
                            addresses,
                            values = addresses.Select(key => snapshot[key]).ToArray(),
                        };
                        break;
                    }
                }

                // --- Remote operations ---
                else if (command == "remote-run") { await client.RemoteRunAsync(); result = new { status = "success" }; }
                else if (command == "remote-stop") { await client.RemoteStopAsync(); result = new { status = "success" }; }
                else if (command == "remote-pause") { await client.RemotePauseAsync(); result = new { status = "success" }; }
                else if (command == "remote-latch-clear") { await client.RemoteLatchClearAsync(); result = new { status = "success" }; }
                else if (command == "remote-reset") { await client.RemoteResetAsync(0x0000, false); result = new { status = "success" }; }

                // --- Random access ---
                else if (command == "random-read")
                {
                    var wDevs = wordDevs.Split(',', StringSplitOptions.RemoveEmptyEntries).Select(SlmpDeviceParser.Parse).ToArray();
                    var dwDevs = dwordDevs.Split(',', StringSplitOptions.RemoveEmptyEntries).Select(SlmpDeviceParser.Parse).ToArray();
                    var (wVals, dwVals) = await client.ReadRandomAsync(wDevs, dwDevs);
                    result = new { status = "success", word_values = wVals, dword_values = dwVals };
                }
                else if (command == "random-write-words")
                {
                    var wItems = ParseKvPairs(wordsKv).Select(p => (SlmpDeviceParser.Parse(p.Key), (ushort)p.Value)).ToArray();
                    var dwItems = ParseKvPairs(dwordsKv).Select(p => (SlmpDeviceParser.Parse(p.Key), (uint)p.Value)).ToArray();
                    await client.WriteRandomWordsAsync(wItems, dwItems);
                    result = new { status = "success" };
                }
                else if (command == "random-write-bits")
                {
                    var bItems = ParseKvPairs(bitsKv).Select(p => (SlmpDeviceParser.Parse(p.Key), p.Value != 0)).ToArray();
                    await client.WriteRandomBitsAsync(bItems);
                    result = new { status = "success" };
                }

                // --- Block access ---
                else if (command == "block-read")
                {
                    var wBlocks = ParseDevCountPairs(wordBlocks).Select(p => new SlmpBlockRead(SlmpDeviceParser.Parse(p.Key), (ushort)p.Value)).ToArray();
                    var bBlocks = ParseDevCountPairs(bitBlocks).Select(p => new SlmpBlockRead(SlmpDeviceParser.Parse(p.Key), (ushort)p.Value)).ToArray();
                    var (wVals, bVals) = await client.ReadBlockAsync(wBlocks, bBlocks);
                    result = new { status = "success", word_values = wVals, bit_values = bVals };
                }
                else if (command == "block-write")
                {
                    var wBlocks = ParseDevValuesPairs(wordBlocks).Select(p => new SlmpBlockWrite(SlmpDeviceParser.Parse(p.Key), p.Value.Select(v => (ushort)v).ToArray())).ToArray();
                    var bBlocks = ParseDevValuesPairs(bitBlocks).Select(p => new SlmpBlockWrite(SlmpDeviceParser.Parse(p.Key), p.Value.Select(v => (ushort)v).ToArray())).ToArray();
                    await client.WriteBlockAsync(wBlocks, bBlocks);
                    result = new { status = "success" };
                }

                // --- Self test ---
                else if (command == "self-test")
                {
                    var data = Encoding.ASCII.GetBytes(string.IsNullOrEmpty(address) ? "TEST" : address);
                    var echoed = await client.SelfTestLoopbackAsync(data);
                    result = new { status = "success", echo = Encoding.ASCII.GetString(echoed) };
                }

                // --- Memory read/write ---
                else if (command == "memory-read")
                {
                    uint head = (uint)SlmpTargetParser.ParseAutoNumber(address);
                    ushort wCount = cmd_args.Count > 0 ? ushort.Parse(cmd_args[0]) : (ushort)1;
                    var vals = await client.MemoryReadWordsAsync(head, wCount);
                    result = new { status = "success", values = vals };
                }
                else if (command == "memory-write")
                {
                    uint head = (uint)SlmpTargetParser.ParseAutoNumber(address);
                    var vals = cmd_args.Select(v => (ushort)int.Parse(v)).ToArray();
                    await client.MemoryWriteWordsAsync(head, vals);
                    result = new { status = "success" };
                }

                // --- Extend unit read/write ---
                else if (command == "extend-unit-read")
                {
                    var parts = address.Split(':');
                    ushort moduleNo = (ushort)SlmpTargetParser.ParseAutoNumber(parts[0]);
                    uint head = parts.Length > 1 ? (uint)SlmpTargetParser.ParseAutoNumber(parts[1]) : 0;
                    ushort wCount = cmd_args.Count > 0 ? ushort.Parse(cmd_args[0]) : (ushort)1;
                    var vals = await client.ExtendUnitReadWordsAsync(head, wCount, moduleNo);
                    result = new { status = "success", values = vals };
                }
                else if (command == "extend-unit-write")
                {
                    var parts = address.Split(':');
                    ushort moduleNo = (ushort)SlmpTargetParser.ParseAutoNumber(parts[0]);
                    uint head = parts.Length > 1 ? (uint)SlmpTargetParser.ParseAutoNumber(parts[1]) : 0;
                    var vals = cmd_args.Select(v => (ushort)int.Parse(v)).ToArray();
                    await client.ExtendUnitWriteWordsAsync(head, moduleNo, vals);
                    result = new { status = "success" };
                }

                // --- Extended address (Extended Device) ---
                else if (command == "read-ext")
                {
                    var qdev = SlmpQualifiedDeviceParser.Parse(address);
                    var ext = new SlmpExtensionSpec();
                    int count = cmd_args.Count > 0 ? int.Parse(cmd_args[0]) : 1;
                    if (mode == "bit")
                    {
                        var vals = await client.ReadBitsExtendedAsync(qdev, (ushort)count, ext);
                        result = new { status = "success", values = vals.Select(v => v ? 1 : 0) };
                    }
                    else
                    {
                        var vals = await client.ReadWordsExtendedAsync(qdev, (ushort)count, ext);
                        result = new { status = "success", values = vals };
                    }
                }
                else if (command == "write-ext")
                {
                    var qdev = SlmpQualifiedDeviceParser.Parse(address);
                    var ext = new SlmpExtensionSpec();
                    if (mode == "bit")
                    {
                        var vals = cmd_args.Select(v => v == "1").ToArray();
                        await client.WriteBitsExtendedAsync(qdev, vals, ext);
                    }
                    else
                    {
                        var vals = cmd_args.Select(v => (ushort)int.Parse(v)).ToArray();
                        await client.WriteWordsExtendedAsync(qdev, vals, ext);
                    }
                    result = new { status = "success" };
                }
            }
            catch (Exception ex) { result = new { status = "error", message = ex.Message }; }
            Console.WriteLine(JsonSerializer.Serialize(result));
        }

        static List<KeyValuePair<string, int>> ParseKvPairs(string s)
        {
            if (string.IsNullOrEmpty(s)) return [];
            return s.Split(',').Select(item =>
            {
                var parts = item.Split('=', 2);
                return new KeyValuePair<string, int>(parts[0].Trim(), int.Parse(parts[1].Trim()));
            }).ToList();
        }

        static List<KeyValuePair<string, int>> ParseDevCountPairs(string s)
        {
            if (string.IsNullOrEmpty(s)) return [];
            return s.Split(',').Select(item =>
            {
                var parts = item.Split('=', 2);
                return new KeyValuePair<string, int>(parts[0].Trim(), int.Parse(parts[1].Trim()));
            }).ToList();
        }

        static List<KeyValuePair<string, List<int>>> ParseDevValuesPairs(string s)
        {
            if (string.IsNullOrEmpty(s)) return [];
            return s.Split(',').Select(item =>
            {
                var parts = item.Split('=', 2);
                var vals = parts[1].Split(':').Select(int.Parse).ToList();
                return new KeyValuePair<string, List<int>>(parts[0].Trim(), vals);
            }).ToList();
        }

        static string[] ParseNamedAddresses(string s)
        {
            if (string.IsNullOrWhiteSpace(s)) return [];
            return s.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        }

        static Dictionary<string, object> ParseNamedUpdates(string s)
        {
            var updates = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (string item in ParseNamedAddresses(s))
            {
                var parts = item.Split('=', 2);
                string key = parts[0].Trim();
                string value = parts[1].Trim();
                updates[key] = ParseNamedScalar(key, value);
            }
            return updates;
        }

        static object ParseNamedScalar(string address, string value)
        {
            if (address.Contains('.'))
                return value == "1" || value.Equals("true", StringComparison.OrdinalIgnoreCase);

            string baseAddress = address.Split(':', 2)[0].Trim();
            var device = SlmpDeviceParser.Parse(baseAddress);
            if (IsBitDevice(device.Code))
                return value == "1" || value.Equals("true", StringComparison.OrdinalIgnoreCase);

            if (address.Contains(':') && address[(address.LastIndexOf(':') + 1)..].Equals("F", StringComparison.OrdinalIgnoreCase))
                return double.Parse(value, System.Globalization.CultureInfo.InvariantCulture);

            return int.Parse(value, System.Globalization.NumberStyles.Integer, System.Globalization.CultureInfo.InvariantCulture);
        }

        static bool IsBitDevice(SlmpDeviceCode code)
            => code is SlmpDeviceCode.SM
                or SlmpDeviceCode.X
                or SlmpDeviceCode.Y
                or SlmpDeviceCode.M
                or SlmpDeviceCode.L
                or SlmpDeviceCode.F
                or SlmpDeviceCode.V
                or SlmpDeviceCode.B
                or SlmpDeviceCode.TS
                or SlmpDeviceCode.TC
                or SlmpDeviceCode.LTS
                or SlmpDeviceCode.LTC
                or SlmpDeviceCode.STS
                or SlmpDeviceCode.STC
                or SlmpDeviceCode.LSTS
                or SlmpDeviceCode.LSTC
                or SlmpDeviceCode.CS
                or SlmpDeviceCode.CC
                or SlmpDeviceCode.LCS
                or SlmpDeviceCode.LCC
                or SlmpDeviceCode.SB
                or SlmpDeviceCode.DX
                or SlmpDeviceCode.DY;
    }
}

