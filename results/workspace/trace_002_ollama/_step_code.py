import pandas as pd
import numpy as np
from scipy.stats import zscore

data = pd.read_excel('c:/Users/t-ashende/Documents/evaluator/temp_excel/02_single_anomaly_subtle.xlsx')
totals = data[['Revenue', 'Units_Sold']].sum()
regional = data.groupby('Region').sum() if 'Region' in data else None
product = data.groupby(['Region', 'Product']).sum() if 'Product' in data else None

if regional and product:
    granular = data.groupby(['Month', 'Region', 'Product']).agg({'Revenue': ['sum', 'count']})
    fin = granular.unstack(level=0, fill_value=0)
    fin['z_scores'] = fin['Revenue_sum'].apply(lambda x: zscore(x))
    anomalies = fin[fin['z_scores'] < -3]
    print("Anomalies:")
    for i, row in anomalies.iterrows():
        print(f"{row['Month']}, {row['Region'] if 'Region' in data else ''}, Product_{row['Product'] if 'Product' in data else ''}: {row['Revenue_sum'][0]}, z-score: {row['z_scores']}")
else:
    granular = data.groupby('Month').agg({'Revenue': ['sum', 'count']})
    fin = granular.unstack(level=0, fill_value=0)
    fin['z_scores'] = fin['Revenue_sum'].apply(lambda x: zscore(x))
    anomalies = fin[fin['z_scores'] < -3]
    print("Anomalies:")
    for i, row in anomalies.iterrows():
        print(f"{row['Month']}: Revenue: {row['Revenue_sum'][0]}, z-score: {row['z_scores']}")

print("Key Findings:")
print(f"Total Revenue: {totals['Revenue'].iloc[0]}")
print(f"Total Units Sold: {totals['Units_Sold'].iloc[0]}" if 'Units_Sold' in data else "")
if regional:
    for region, total in regional.iteritems():
        print(f"{region}: Total Revenue: {total['Revenue']}, Total Units Sold: {total['Units_Sold']}")
if product:
    for product, total in product.iteritems():
        print(f"Product_{product}: Total Revenue: {total['Revenue']}, Total Units Sold: {total['Units_Sold']}")