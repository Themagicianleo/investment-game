"""Historical scenarios: replay a well-known market period with a briefing and debrief.

Briefings set the scene as it looked at the time and do not reveal what came
next. Debriefs, shown only after the session ends, describe what happened.
Figures are approximate, commonly cited peak-to-trough index moves.
"""

from __future__ import annotations

SCENARIOS: list[dict] = [
    {
        "id": "dotcom",
        "title": "The dot-com era",
        "period": "From March 2000",
        "start": "2000-03-01",
        "days": 504,
        "tickers": ["SPY", "MSFT", "INTC", "CSCO", "AAPL"],
        "benchmark": "SPY",
        "difficulty": "Hard",
        "briefing": (
            "It is early March 2000. Technology stocks have had a spectacular run; internet and networking companies "
            "trade at very high valuations, and many people are convinced 'this time is different'. You have "
            "$10,000 and two years. Focus on process: size positions so that being wrong is survivable, and write "
            "down what would prove you wrong before each trade."
        ),
        "debrief": (
            "The Nasdaq Composite peaked on 10 March 2000 and fell roughly 78% by October 2002; the S&P 500 fell "
            "about 49%. Many leading tech stocks lost 80–90%, and some took more than a decade to recover, or never "
            "did. Concentrated positions in favourite tech names were extremely painful, and broad diversification "
            "softened the blow. The lesson people most often draw is about valuation, concentration and having an "
            "exit plan, not about predicting the top."
        ),
        "lessons": ["sizing", "biases", "drawdowns"],
    },
    {
        "id": "gfc",
        "title": "The global financial crisis",
        "period": "From October 2007",
        "start": "2007-10-01",
        "days": 504,
        "tickers": ["SPY", "AAPL", "JPM", "XOM", "GLD"],
        "benchmark": "SPY",
        "difficulty": "Hard",
        "briefing": (
            "It is October 2007. US stocks are near all-time highs. There are worries about subprime mortgages and some "
            "funds have run into trouble, but the economy is still growing. You can trade a broad S&P 500 ETF, three "
            "large companies from different industries, and a gold ETF. Pay attention to how your holdings move "
            "together."
        ),
        "debrief": (
            "From its October 2007 peak the S&P 500 fell about 57% to its March 2009 low as the housing bust turned "
            "into a banking crisis; Lehman Brothers failed in September 2008. Bank stocks were hit especially hard. "
            "Assets that usually move differently became more correlated in the panic, though gold behaved "
            "differently from stocks for much of the period. Markets began recovering in March 2009, long before the "
            "news improved, which is why selling in a panic near the low was so costly."
        ),
        "lessons": ["drawdowns", "sizing", "exit-plans"],
    },
    {
        "id": "covid",
        "title": "The 2020 pandemic shock",
        "period": "From January 2020",
        "start": "2020-01-02",
        "days": 252,
        "tickers": ["VT", "AAPL", "NVDA", "INTC", "AMD"],
        "benchmark": "VT",
        "difficulty": "Medium",
        "briefing": (
            "It is the start of 2020. Markets had a strong 2019. There are early reports of a new virus in China. You "
            "have one year. Notice how you feel and what you write in your journal if markets move fast."
        ),
        "debrief": (
            "Between 19 February and 23 March 2020 the S&P 500 fell about 34%, one of the fastest crashes on record. "
            "Massive central-bank and government support followed, and the index regained its old high by August "
            "2020. Some technology and chip stocks ended the year far higher than they started. Selling near the "
            "March low locked in losses just before a sharp rebound, a classic example of how hard it is to time "
            "both the exit and the re-entry."
        ),
        "lessons": ["biases", "exit-plans", "drawdowns"],
    },
    {
        "id": "bear2022",
        "title": "The 2022 rate-hike bear market",
        "period": "From November 2021",
        "start": "2021-11-01",
        "days": 300,
        "tickers": ["VT", "AAPL", "NVDA", "INTC", "AMD"],
        "benchmark": "VT",
        "difficulty": "Medium",
        "briefing": (
            "It is November 2021. Stocks, especially technology and semiconductor companies, have risen strongly since "
            "2020. Inflation is running well above target and central banks have signalled they may raise interest "
            "rates. Think about how a change in interest rates could affect fast-growing companies' valuations."
        ),
        "debrief": (
            "In 2022 the US Federal Reserve raised its policy rate from near zero to over 4%. The S&P 500 fell about "
            "25% from its January peak to its October low, and the tech-heavy Nasdaq about a third. Highly valued growth "
            "and semiconductor stocks fell much further. Unlike 2020 there was no quick rebound within the year, so "
            "patience and position sizing mattered more than speed."
        ),
        "lessons": ["drawdowns", "trend-following", "benchmarks"],
    },
]

SCENARIO_BY_ID = {s["id"]: s for s in SCENARIOS}


def public_scenarios() -> list[dict]:
    """Scenario list without the debrief (shown only after the session ends)."""
    return [{k: v for k, v in s.items() if k != "debrief"} for s in SCENARIOS]
