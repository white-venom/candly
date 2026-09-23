import pytest

from candly.news.sentiment import METHOD, event_type, lexicon_sentiment


def test_method_name():
    assert METHOD == "lexicon"


@pytest.mark.parametrize(
    ("text", "sign"),
    [
        ("Infosys shares surge after strong Q2 results", 1),
        ("TCS stock hits record high", 1),
        ("HDFC Bank shares slump as margins weaken", -1),
        ("SBI posts net loss; stock plunges", -1),
        ("Stock falls on profit booking", -1),
        ("Company does not miss estimates", 1),
        ("Board meeting scheduled on Friday", 0),
    ],
)
def test_sentiment_sign(text, sign):
    score = lexicon_sentiment(text)
    assert -1 < score < 1
    assert (score > 0) - (score < 0) == sign


def test_more_cues_mean_stronger_scores():
    assert lexicon_sentiment("shares surge") < lexicon_sentiment("shares surge and rally to record gains")
    assert lexicon_sentiment("") == 0.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Reliance Q2 results: net profit rises 9%", "results"),
        ("Jefferies upgrades Axis Bank, raises target price", "rating_change"),
        ("L&T bags mega order worth Rs 5,000 crore", "order_win"),
        ("SEBI imposes penalty on broker", "regulatory"),
        ("Company to acquire 51% stake in startup", "deal"),
        ("CEO resigns with immediate effect", "management"),
        ("RBI keeps repo rate unchanged, inflation eases", "macro"),
        ("Shares trade flat in a quiet session", None),
    ],
)
def test_event_types(text, expected):
    assert event_type(text) == expected
