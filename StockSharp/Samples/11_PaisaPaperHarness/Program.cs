using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

if (args.Length == 0)
{
	Console.WriteLine("Usage: PaisaPaperHarness <path-to-manifest.json>");
	return 1;
}

var manifestPath = Path.GetFullPath(args[0]);
if (!File.Exists(manifestPath))
{
	Console.WriteLine($"Manifest not found: {manifestPath}");
	return 1;
}

var manifest = JsonSerializer.Deserialize<BridgeManifest>(File.ReadAllText(manifestPath), new JsonSerializerOptions
{
	PropertyNameCaseInsensitive = true,
});

if (manifest?.Files is null || manifest.Files.Count == 0)
{
	Console.WriteLine("Manifest contains no symbol files.");
	return 1;
}

var initialCash = manifest.BrokerConfig?.InitialCash ?? 100_000m;
var spreadBps = manifest.BrokerConfig?.SpreadBps ?? 3m;
var slippageBps = manifest.BrokerConfig?.SlippageBps ?? 2m;
var maxPositionPct = manifest.BrokerConfig?.MaxPositionPct ?? 0.20m;

Console.WriteLine("Paisa StockSharp Bridge Paper Harness");
Console.WriteLine($"Manifest: {manifestPath}");
Console.WriteLine($"Strategy: {manifest.Strategy}");
Console.WriteLine($"Initial cash per symbol: {initialCash:N2}");
Console.WriteLine();

foreach (var (symbol, files) in manifest.Files)
{
	var candles = ReadCandles(files.Candles);
	var signals = ReadSignals(files.Signals).ToDictionary(s => s.Time);
	var broker = new PaperBroker(initialCash, spreadBps, slippageBps);
	var previousTarget = 0m;

	foreach (var candle in candles)
	{
		if (!signals.TryGetValue(candle.Time, out var signal))
			continue;

		var target = Math.Clamp(signal.TargetPosition, 0m, 1m);
		if (target == previousTarget)
			continue;

		var equity = broker.MarkToMarket(candle.Close);
		var desiredQty = (int)Math.Floor((equity * maxPositionPct * target) / candle.Open);
		var currentQty = broker.Position;

		if (target > previousTarget)
		{
			var qty = Math.Max(0, desiredQty - currentQty);
			broker.Fill(candle.Time, symbol, "BUY", qty, candle.Open, signal.Reason);
		}
		else
		{
			var qty = target == 0m ? currentQty : Math.Max(0, currentQty - desiredQty);
			broker.Fill(candle.Time, symbol, "SELL", qty, candle.Open, signal.Reason);
		}

		previousTarget = target;
	}

	if (broker.Position > 0 && candles.Count > 0)
	{
		var last = candles[^1];
		broker.Fill(last.Time, symbol, "SELL", broker.Position, last.Close, "final_flatten");
	}

	var finalEquity = candles.Count == 0 ? broker.Cash : broker.MarkToMarket(candles[^1].Close);
	Console.WriteLine($"{symbol}: final equity={finalEquity:N2}, trades={broker.Trades}, costs={broker.Costs:N2}");
}

return 0;

static List<CandleRow> ReadCandles(string path)
	=> File.ReadLines(path)
		.Skip(1)
		.Where(line => !string.IsNullOrWhiteSpace(line))
		.Select(line =>
		{
			var p = line.Split(',');
			return new CandleRow(
				p[0],
				DateTime.Parse(p[1], CultureInfo.InvariantCulture),
				decimal.Parse(p[2], CultureInfo.InvariantCulture),
				decimal.Parse(p[3], CultureInfo.InvariantCulture),
				decimal.Parse(p[4], CultureInfo.InvariantCulture),
				decimal.Parse(p[5], CultureInfo.InvariantCulture),
				decimal.Parse(p[6], CultureInfo.InvariantCulture));
		})
		.ToList();

static List<SignalRow> ReadSignals(string path)
	=> File.ReadLines(path)
		.Skip(1)
		.Where(line => !string.IsNullOrWhiteSpace(line))
		.Select(ParseSignal)
		.ToList();

static SignalRow ParseSignal(string line)
{
	var p = line.Split(',');
	return new SignalRow(
		p[0],
		DateTime.Parse(p[1], CultureInfo.InvariantCulture),
		decimal.Parse(p[2], CultureInfo.InvariantCulture),
		decimal.Parse(p[3], CultureInfo.InvariantCulture),
		p[4],
		p.Length > 5 ? p[5] : "",
		p.Length > 6 ? p[6] : "");
}

record CandleRow(string Symbol, DateTime Time, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume);
record SignalRow(string Symbol, DateTime Time, decimal Close, decimal TargetPosition, string Action, string Reason, string Strategy);

sealed class PaperBroker
{
	private readonly decimal _spreadBps;
	private readonly decimal _slippageBps;

	public PaperBroker(decimal initialCash, decimal spreadBps, decimal slippageBps)
	{
		Cash = initialCash;
		_spreadBps = spreadBps;
		_slippageBps = slippageBps;
	}

	public decimal Cash { get; private set; }
	public int Position { get; private set; }
	public int Trades { get; private set; }
	public decimal Costs { get; private set; }

	public void Fill(DateTime time, string symbol, string side, int quantity, decimal referencePrice, string reason)
	{
		if (quantity <= 0)
			return;

		if (side == "SELL")
			quantity = Math.Min(quantity, Position);

		if (quantity <= 0)
			return;

		var price = FillPrice(side, referencePrice);
		var gross = price * quantity;
		var costs = EstimateCosts(gross, side);

		if (side == "BUY")
		{
			if (gross + costs > Cash)
				return;

			Cash -= gross + costs;
			Position += quantity;
		}
		else
		{
			Cash += gross - costs;
			Position -= quantity;
		}

		Costs += costs;
		Trades++;
		Console.WriteLine($"  {time:yyyy-MM-dd HH:mm:ss} {symbol} {side} {quantity} @ {price:N2} costs={costs:N2} reason={reason}");
	}

	public decimal MarkToMarket(decimal price) => Cash + Position * price;

	private decimal FillPrice(string side, decimal referencePrice)
	{
		var halfSpread = _spreadBps / 2m / 10_000m;
		var slip = _slippageBps / 10_000m;
		return side == "BUY"
			? referencePrice * (1m + halfSpread + slip)
			: referencePrice * (1m - halfSpread - slip);
	}

	private static decimal EstimateCosts(decimal gross, string side)
	{
		const decimal exchangeTxnBps = 0.307m;
		const decimal sebiBps = 0.01m;
		const decimal stampBuyBps = 0.3m;
		const decimal sttSellBps = 2.5m;
		const decimal gstRate = 0.18m;

		var exchange = gross * exchangeTxnBps / 10_000m;
		var sebi = gross * sebiBps / 10_000m;
		var stamp = side == "BUY" ? gross * stampBuyBps / 10_000m : 0m;
		var stt = side == "SELL" ? gross * sttSellBps / 10_000m : 0m;
		var gst = (exchange + sebi) * gstRate;
		return exchange + sebi + stamp + stt + gst;
	}
}

sealed class BridgeManifest
{
	[JsonPropertyName("strategy")]
	public string? Strategy { get; set; }

	[JsonPropertyName("broker_config")]
	public BrokerConfigDto? BrokerConfig { get; set; }

	[JsonPropertyName("files")]
	public Dictionary<string, BridgeFiles> Files { get; set; } = new();
}

sealed class BrokerConfigDto
{
	[JsonPropertyName("initial_cash")]
	public decimal InitialCash { get; set; }

	[JsonPropertyName("spread_bps")]
	public decimal SpreadBps { get; set; }

	[JsonPropertyName("slippage_bps")]
	public decimal SlippageBps { get; set; }

	[JsonPropertyName("max_position_pct")]
	public decimal MaxPositionPct { get; set; }
}

sealed class BridgeFiles
{
	[JsonPropertyName("candles")]
	public string Candles { get; set; } = "";

	[JsonPropertyName("signals")]
	public string Signals { get; set; } = "";

	[JsonPropertyName("equity")]
	public string Equity { get; set; } = "";

	[JsonPropertyName("fills")]
	public string Fills { get; set; } = "";
}
