from paisa_trader import sentiment
from paisa_trader.sentiment import get_dummy_sentiment, score_headline, score_headlines


def test_score_headline_positive(monkeypatch):
    monkeypatch.setattr(sentiment, "load_finbert", lambda: None)

    result = score_headline("Markets rally on strong GDP data")

    assert result["label"] == "positive"
    assert result["numeric"] > 0


def test_score_headline_negative(monkeypatch):
    monkeypatch.setattr(sentiment, "load_finbert", lambda: None)

    result = score_headline("Stocks crash amid recession fears")

    assert result["label"] == "negative"
    assert result["numeric"] < 0


def test_score_headlines_empty_is_neutral():
    result = score_headlines([])

    assert result["composite"] == 0.0
    assert result["dominant"] == "neutral"


def test_get_dummy_sentiment():
    result = get_dummy_sentiment()

    assert result["composite"] == 0.0
    assert result["bullish_count"] == 0
    assert result["bearish_count"] == 0
    assert result["neutral_count"] == 0
