# Roll exits, not back-adjustment

Our continuous series stays unadjusted, so prices match TradingView's default continuous chart. A trade open at a roll is closed at the last bar before it, and signals in the 60 minutes before a roll are discarded. Without this, the price difference between contracts reaches trades as a fake gap: on MNQ it averaged 77 points and reached 273 between 2019 and mid-2024; on MES it averaged 22.

## Considered Options

- **Back-adjust the series** (what the price action lab does): removes the jump from indicators too, but shifts every historical price away from what TradingView shows, which breaks Pine comparisons and absolute levels. Revisit if indicator jumps at rolls show up in real results.
- **Roll handling for yfinance data**: not possible, because yfinance continuous futures don't expose their roll dates. Those series keep hidden roll gaps.
