import json
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

# Load handoff data
with open('c:\\Users\\t-ashende\\Documents\\evaluator\\results\\workspace\\trace_002_groq\\handoff_data.json') as f:
    handoff_data = json.load(f)

# Load existing Word document
document = Document('c:/Users/t-ashende/Documents/evaluator/results/workspace/trace_002_groq/report.docx')

# Replace placeholder paragraphs with real narrative
for paragraph in document.paragraphs:
    if 'EXECUTIVE_SUMMARY' in paragraph.text:
        paragraph.text = f'Executive Summary: Total Revenue is {handoff_data.get("Total Revenue", "Not Available")}, Total Units is {handoff_data.get("Total Units", "Not Available")}, and the top performing region is {handoff_data.get("Region", "Not Available")} with {handoff_data.get("Value", "Not Available")} in sales.'
    elif 'ANOMALY_SECTION' in paragraph.text:
        paragraph.text = f'Anomaly Section: There is an anomaly in the {handoff_data.get("Region", "Not Available")} region with {handoff_data.get("Product", "Not Available")} product in {handoff_data.get("Month", "Not Available")} month, with a value of {handoff_data.get("Value", "Not Available")} and a Z-Score of {handoff_data.get("Z-Score", "Not Available")}.'
    elif 'RECOMMENDATIONS' in paragraph.text:
        paragraph.text = f'Recommendations: Increase marketing efforts in the {handoff_data.get("Region", "Not Available")} region, optimize product {handoff_data.get("Product", "Not Available")} pricing, and provide additional training to sales teams in the {handoff_data.get("Region", "Not Available")} region.'

# Save the completed document
document.save('c:/Users/t-ashende/Documents/evaluator/results/workspace/trace_002_groq/report.docx')

# Print JSON block
executive_summary = f'Total Revenue is {handoff_data.get("Total Revenue", "Not Available")}, Total Units is {handoff_data.get("Total Units", "Not Available")}, and the top performing region is {handoff_data.get("Region", "Not Available")} with {handoff_data.get("Value", "Not Available")} in sales.'
anomaly_section = f'There is an anomaly in the {handoff_data.get("Region", "Not Available")} region with {handoff_data.get("Product", "Not Available")} product in {handoff_data.get("Month", "Not Available")} month, with a value of {handoff_data.get("Value", "Not Available")} and a Z-Score of {handoff_data.get("Z-Score", "Not Available")}.'
recommendations = [f'Increase marketing efforts in the {handoff_data.get("Region", "Not Available")} region', f'Optimize product {handoff_data.get("Product", "Not Available")} pricing', f'Provide additional training to sales teams in the {handoff_data.get("Region", "Not Available")} region']
print('<<<JSON_START>>>')
print(json.dumps({
    "executive_summary": executive_summary,
    "anomaly_section": anomaly_section,
    "recommendations": recommendations
}))
print('<<<JSON_END>>>')