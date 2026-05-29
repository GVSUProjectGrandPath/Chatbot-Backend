import sys
import os
import pandas as pd

# Add the project root to python path to import src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.nodes.ferpa_sanitizer_node import ferpa_sanitizer

def run_tests():
    csv_file = '/Users/puneeth/Desktop/FinLit-Bot/test/ferpa_additional_test_cases.csv'
    mismatched_file='/Users/puneeth/Desktop/FinLit-Bot/test/ferpa_test_failed_cases.csv'
    df = pd.read_csv(csv_file)
    
    outputs = []
    for text in df['text']:
        # Ensure text is a string to avoid regex errors on empty cells (NaN)
        result = ferpa_sanitizer(str(text)) 
        outputs.append(result)
        
    df['model_output'] = outputs
    df_mismatched= df[df['model_output']!=df['ferpa_blocked']]
    df.to_csv(csv_file, index=False)
    df_mismatched.to_csv(mismatched_file, index=False)
    print("Successfully appended output to ferpa_test_cases.csv")


    

if __name__ == "__main__":
    run_tests()
