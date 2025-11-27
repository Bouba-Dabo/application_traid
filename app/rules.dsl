# Example DSL rules
# Format: IF <expression> THEN <ACTION> # optional comment
# Use indicator names like RSI, MACD, SMA20, SMA50, STOCH_K, BBU, BBL

IF RSI > 70 THEN SELL # Overbought
IF RSI < 30 THEN BUY # Oversold
IF MACD < 0 AND MACD_SIGNAL < 0 AND RSI > 65 THEN SELL # bearish momentum
IF SMA20 > SMA50 AND RSI > 50 THEN BUY # uptrend + momentum
IF Close < BBL THEN BUY # price touched lower Bollinger

# Additional example score-based rules to fine-tune sensitivity
# Penalize moderate overbought (RSI > 65)
IF RSI > 65 THEN -2 # Penalise surachat
# Reward clear oversold (RSI < 30)
IF RSI < 30 THEN +2 # Récompense survente
