import json
from docx import Document

# Load handoff data
with open(r'c:\Users\t-ashende\Documents\evaluator\results\workspace\trace_002_qwen\handoff_data.json') as f:
    handoff_data = json.load(f)

# Process data to find anomalies and key metrics
sales_data = handoff_data.get('sales_data', [])
anomalies = []
for region in set(d['region'] for d in sales_data):
    for product in set(d['product'] for d in sales_data):
        region_product_data = [d for d in sales_data if d['region'] == region and d['product'] == product]
        region_product_data.sort(key=lambda x: x['month'])
        for i in range(1, len(region_product_data)):
            prev_sales = region_product_data[i-1]['