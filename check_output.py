import re

with open(r'C:\Users\zafra\Desktop\humanizer\fyp_humanized.txt', 'r', encoding='utf-8') as f:
    text = f.read()

garbage = ['input text missing', 'cant edit', 'Send text', 'Significant expansion', '(1) 2.', '(2) 3.']
found_garbage = [g for g in garbage if g in text]

checks = ['Figure 2.3', 'Figure 3.4', 'Figure 3.7', 'Table 4.1', 'UC-01', 'UC-13', 'Roslina', 'UMP']
preserved = [(c, c in text) for c in checks]

casual = ['Honestly', 'I think', 'Big deal', 'Fair point', 'you know', 'I mean']
leaks = [(c, c in text) for c in casual]

print(f'Words: {len(text.split())}')
garbage_str = ', '.join(found_garbage) if found_garbage else 'NONE (clean)'
print(f'Garbage found: {garbage_str}')
print()
print('Key references:')
for c, found in preserved:
    mark = 'Y' if found else 'N'
    print(f'  {c}: {mark}')
print()
print('Casual leaks:')
for c, found in leaks:
    mark = 'Y' if found else 'N'
    print(f'  {c}: {mark}')
print()
print('FIRST 400 CHARS:')
print(text[:400])
print()
mid = len(text) // 2
print(f'MIDDLE (char {mid}):')
print(text[mid:mid+400])
print()
print('LAST 400 CHARS:')
print(text[-400:])
