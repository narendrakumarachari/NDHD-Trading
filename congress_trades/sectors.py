"""Ticker -> sector lookup and tickers the source could not resolve.

Hand-maintained for the sample. The daily job should replace this with a
reviewed reference file (or the broker's asset endpoint) rather than grow it
by hand; anything missing falls back to "Other".
"""

UNRESOLVED = {
    # Aggregator showed a truncated name or a ticker that does not match the
    # filed asset (e.g. "BRK" on a Barclays Bank PLC structured note). These
    # are kept, flagged needs_review, and left out of stock charts.
    "BRK", "Q", "RA", "MRSH", "SCHO", "MBGL", "AMRZ", "SPCX", "RYT", "HONA",
}

_GROUPS = {
    "Technology": "NVDA AAPL ACN ADBE ADI APP AVGO CDNS CRDO CRM CRWD CTSH FN FTNT HUBS LITE LRCX MCHP MDB MRVL MSFT NOW NXPI PRGS PTC QCOM SMTC STX TTD VIAV VUZI SAIL FISV FLT",
    "Communication": "CHTR CMCSA DIS EA FWONK GOOGL LYV NFLX PINS PSKY T TMUS TTWO VSAT",
    "Consumer": "MODG AMZN BABA CL COCO DASH F GAP HD HRL JD KMB KO LOW LTH LVS MCD MO NKE ORLY PEP PG SBUX SCI SFM TJX TSCO TSLA VIK WMT",
    "Healthcare": "ALC ALKS ALNY AMGN BIIB BSX CNC CRNX DHR GILD HQY ICLR IQV ISRG JNJ LH MCK MDT MRK NVO NVS PCVX PRAX SDZNY STE TMO UNH VRTX WAT ZTS",
    "Financials": "BRK.B ABCB AFL ALL APO BLK BNS CB FDS FITB GBCI HMN JPM SF UPST V WFC",
    "Industrials": "AVAV BA BWXT CARR CHRW CR CSX DCO EMR EPAC ETN FDX FERG HON HUBB ITW LMT MIDD OTIS PWR ROK TDY UPS VLTO WAB",
    "Energy": "DVN MPC OKE PBA TRP XOM",
    "Utilities": "AEP CEG CNP NEE FSLR",
    "Materials": "MSB BCPC DD FCX LIN MLM MTX VMC",
    "Real estate": "AMT EQIX IRT NHI NSA",
    "Funds": "AIO ETV",
    "Homebuilders": "BLDR LEN",
}

SECTORS = {t: sector for sector, tickers in _GROUPS.items() for t in tickers.split()}
