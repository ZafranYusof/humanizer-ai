"""Fix all indentation issues in app_v5.py caused by build script replacements."""
import re

with open(r"C:\Users\zafra\Desktop\humanizer\app_v5.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find all lines with wrong indentation after deduplicate_overlaps
fixed = 0
i = 0
while i < len(lines):
    line = lines[i]
    
    # Pattern: after "processed_chunks = deduplicate_overlaps(processed_chunks)" 
    # the next non-empty line should be at same indent level
    if 'processed_chunks = deduplicate_overlaps(processed_chunks)' in line:
        # Get the indentation of this line
        indent = len(line) - len(line.lstrip())
        
        # Fix next non-empty lines until we hit a blank line or same-level code
        j = i + 1
        while j < len(lines):
            next_line = lines[j]
            if next_line.strip() == '':
                j += 1
                continue
            
            # Check if this line has too much indentation
            next_indent = len(next_line) - len(next_line.lstrip())
            if next_indent > indent and not next_line.strip().startswith('#'):
                # This line belongs to the same block, fix its indentation
                if next_indent > indent + 4:
                    lines[j] = ' ' * (indent + 4) + next_line.lstrip()
                    fixed += 1
            else:
                break
            j += 1
    
    i += 1

# Also fix the specific issue: lines after deduplicate_overlaps that have 12 spaces
# should have 4 spaces (function body level)
for i in range(len(lines)):
    if lines[i].startswith('            result = smooth_transitions'):
        lines[i] = '    result = smooth_transitions(processed_chunks, tone=tone)\n'
        fixed += 1
    elif lines[i].startswith('            if tone != '):
        lines[i] = '    ' + lines[i].lstrip()
        fixed += 1
    elif lines[i].startswith('            result = ultra_short'):
        lines[i] = '        ' + lines[i].lstrip()
        fixed += 1
    elif lines[i].startswith('            result = rhetorical'):
        lines[i] = '        ' + lines[i].lstrip()
        fixed += 1
    elif lines[i].startswith('            result = _strip_casual'):
        lines[i] = '        ' + lines[i].lstrip()
        fixed += 1
    elif lines[i].startswith('            result = paragraph_vary'):
        lines[i] = '    ' + lines[i].lstrip()
        fixed += 1
    elif lines[i].startswith('            result = re.sub'):
        lines[i] = '    ' + lines[i].lstrip()
        fixed += 1

print(f"Fixed {fixed} indentation issues")

with open(r"C:\Users\zafra\Desktop\humanizer\app_v5.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

# Verify
import ast
try:
    ast.parse(''.join(lines))
    print("✓ Syntax OK")
except SyntaxError as e:
    print(f"✗ Still has error: {e}")
