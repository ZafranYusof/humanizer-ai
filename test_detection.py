"""
Test what AI detectors actually look for:
1. Perplexity (low = AI)
2. Burstiness (uniform sentence length = AI)
3. Token probability distribution
4. Repetitive patterns
"""
import re, math
from collections import Counter

def calc_burstiness(text):
    """Calculate sentence length variation (AI = low burstiness)"""
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
    if len(sentences) < 2:
        return 0
    
    lengths = [len(s.split()) for s in sentences]
    mean = sum(lengths) / len(lengths)
    variance = sum((l - mean)**2 for l in lengths) / len(lengths)
    std = math.sqrt(variance)
    cv = std / mean if mean > 0 else 0  # coefficient of variation
    
    return {
        'cv': round(cv, 3),
        'min': min(lengths),
        'max': max(lengths),
        'mean': round(mean, 1),
        'std': round(std, 1),
        'sentences': len(sentences),
        'unique_lengths': len(set(lengths)),
    }

def calc_repetition(text):
    """Check for repetitive patterns (AI tell)"""
    words = text.lower().split()
    bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
    trigrams = [f"{words[i]} {words[i+1]} {words[i+2]}" for i in range(len(words)-2)]
    
    bigram_counts = Counter(bigrams)
    trigram_counts = Counter(trigrams)
    
    # Count repeated bigrams (excluding common ones)
    common = {'of the', 'in the', 'to the', 'and the', 'for the', 'is a', 'has been', 'it is'}
    repeated_bi = {k: v for k, v in bigram_counts.items() if v > 2 and k not in common}
    
    return {
        'repeated_bigrams': len(repeated_bi),
        'worst_bigrams': dict(list(repeated_bi.items())[:5]),
        'vocab_richness': len(set(words)) / len(words) if words else 0,
    }

def calc_ai_tells(text):
    """Count AI-specific patterns"""
    tells = {
        'transition_words': len(re.findall(r'\b(Furthermore|Moreover|Additionally|Consequently|Nevertheless|In addition|In conclusion)\b', text, re.I)),
        'ai_verbs': len(re.findall(r'\b(delve|leverage|utilize|facilitate|streamline|underscore|foster)\b', text, re.I)),
        'ai_adjectives': len(re.findall(r'\b(comprehensive|robust|multifaceted|holistic|pivotal|crucial|paramount|seamless)\b', text, re.I)),
        'passive_voice': len(re.findall(r'\b(was|were|been|being)\s+\w+ed\b', text, re.I)),
        'perfect_grammar': 0,  # hard to check without NLP
        'avg_word_length': round(sum(len(w) for w in text.split()) / len(text.split()), 1),
        'no_contractions': len(re.findall(r"\b(do not|does not|is not|are not|was not|were not|will not|would not|cannot|can not|have not|has not|had not|it is|that is|there is)\b", text, re.I)),
    }
    return tells

# Test with typical AI text
ai_text = """The proliferation of digital technologies has fundamentally transformed the way businesses operate in the contemporary era. Organizations across various sectors are increasingly adopting innovative technological solutions to streamline their operations, enhance productivity, and maintain a competitive edge in the global marketplace. This paradigm shift has necessitated a comprehensive reevaluation of traditional business models, as companies must now leverage digital tools and platforms to effectively engage with customers, optimize supply chains, and drive sustainable growth in an increasingly interconnected world."""

print("=== AI TEXT ANALYSIS ===")
print(f"\nBurstiness: {calc_burstiness(ai_text)}")
print(f"\nRepetition: {calc_repetition(ai_text)}")
print(f"\nAI Tells: {calc_ai_tells(ai_text)}")

# Now test with human-like text
human_text = """Digital tech? It's changed everything, honestly. Companies everywhere — I mean literally everywhere — are scrambling to adopt new tools. Some are killing it. Others? Not so much. The thing is, you can't just throw money at the problem and expect magic. It's more complicated than that, and honestly, most businesses still don't get it."""

print("\n\n=== HUMAN TEXT ANALYSIS ===")
print(f"\nBurstiness: {calc_burstiness(human_text)}")
print(f"\nRepetition: {calc_repetition(human_text)}")
print(f"\nAI Tells: {calc_ai_tells(human_text)}")
