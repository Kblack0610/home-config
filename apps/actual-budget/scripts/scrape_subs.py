import pandas as pd

# Load your export
df = pd.read_csv('transactions.csv')

# Convert date and ensure amounts are absolute (ignore negative signs)
df['date'] = pd.to_datetime(df['date'])
df['abs_amount'] = df['amount'].abs()

# 1. Group by Payee and Amount
# Recurring subs usually have the exact same amount.
recurring = df.groupby(['payee', 'abs_amount']).size().reset_index(name='count')

# 2. Filter for potential subs
# Look for things that happened at least 3 times (monthly) or 1 time (yearly audit)
potential_subs = recurring[recurring['count'] >= 3].sort_values(by='count', ascending=False)

print(potential_subs)
