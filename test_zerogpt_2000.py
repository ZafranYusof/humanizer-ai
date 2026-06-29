"""Test humanizer on 2000+ word text against ZeroGPT"""
import json, urllib.request, time

# Build 2000+ words by doubling the paragraphs
paras_base = [
    "The proliferation of digital technologies has fundamentally transformed the way businesses operate in the contemporary era. Organizations across various sectors are increasingly adopting innovative technological solutions to streamline their operations, enhance productivity, and maintain a competitive edge in the global marketplace. This paradigm shift has necessitated a comprehensive reevaluation of traditional business models, as companies must now leverage digital tools and platforms to effectively engage with customers, optimize supply chains, and drive sustainable growth in an increasingly interconnected world where geographical boundaries no longer constrain commercial activities.",
    "Furthermore, the integration of artificial intelligence and machine learning algorithms has revolutionized data processing capabilities, enabling organizations to derive actionable insights from vast datasets that were previously impossible to analyze manually. These sophisticated analytical tools facilitate the identification of patterns, trends, and correlations that would otherwise remain obscured within complex information ecosystems. Consequently, decision-making processes have become more data-driven, allowing businesses to respond proactively to market dynamics and consumer preferences with unprecedented speed and accuracy that fundamentally reshapes competitive dynamics across industries.",
    "Moreover, the advent of cloud computing has democratized access to powerful computational resources, eliminating the need for substantial upfront infrastructure investments that traditionally served as barriers to entry for smaller organizations. Small and medium enterprises can now utilize enterprise-grade technologies on a pay-as-you-go basis, thereby leveling the competitive playing field against larger corporations with deeper financial resources and established market positions. This accessibility has fostered innovation across diverse industries, as organizations can rapidly prototype, test, and deploy new solutions without the constraints of traditional IT limitations and bureaucratic approval processes.",
    "Additionally, the rise of e-commerce platforms has fundamentally altered consumer behavior and expectations in profound and lasting ways that continue to evolve with each technological advancement. Modern consumers demand seamless, personalized experiences across multiple touchpoints, requiring businesses to implement omnichannel strategies that integrate online and offline interactions seamlessly and consistently throughout the entire customer journey. This shift has compelled retailers to invest heavily in digital infrastructure, customer relationship management systems, and advanced analytics to deliver tailored recommendations and targeted marketing campaigns that resonate with individual preferences.",
    "In addition, cybersecurity has emerged as a critical concern in the digital transformation landscape that cannot be overlooked by any organization regardless of size, industry, or geographic location. As companies increasingly rely on digital systems and store sensitive data electronically, they face heightened risks from sophisticated cyber threats that evolve constantly and become more dangerous with each passing year. Implementing robust security measures, including encryption protocols, multi-factor authentication systems, and continuous monitoring solutions, has become absolutely essential to protect against data breaches, ransomware attacks, and other malicious activities that could compromise business continuity.",
    "The Internet of Things represents another transformative technology that is reshaping industries from manufacturing to healthcare in remarkable and unexpected ways that few analysts predicted even a decade ago. By connecting physical devices to the internet, organizations can monitor operations in real-time, predict equipment failures before they occur, and optimize resource allocation across entire production lines with remarkable precision and efficiency. This connectivity enables predictive maintenance strategies, reduces downtime significantly, and enhances overall operational efficiency while creating entirely new revenue streams through data-driven services and innovative product offerings that were previously unimaginable.",
    "Social media platforms have also become indispensable tools for brand building and customer engagement in the modern business environment where attention has become the scarcest and most valuable resource. Companies leverage these platforms to share compelling content, respond to customer inquiries promptly, and build thriving communities around their products and services effectively and authentically. The viral nature of social media allows businesses to reach millions of potential customers at a fraction of the cost of traditional advertising methods, making it an essential component of any comprehensive marketing strategy in the digital age.",
    "Furthermore, the emergence of blockchain technology has introduced exciting new possibilities for secure, transparent transactions without the need for intermediaries that traditionally add cost and complexity to business processes. This decentralized approach to record-keeping has applications far beyond cryptocurrency, including supply chain verification, smart contracts, and digital identity management systems that enhance security and reduce fraud. Organizations are actively exploring these use cases to enhance trust, reduce fraudulent activities, and improve operational transparency across their entire value chain and extensive partner networks spanning multiple jurisdictions.",
]

paras_extra = [
    "The widespread adoption of robotic process automation has streamlined repetitive tasks across various business functions, from finance and accounting to human resources and beyond the traditional back office environment. By automating routine processes, organizations can redirect human talent toward more strategic, creative endeavors that require critical thinking and complex problem-solving skills that machines cannot yet replicate effectively. This shift not only improves operational efficiency but also enhances employee satisfaction by eliminating monotonous work and allowing staff to focus on more meaningful contributions to organizational goals and long-term strategic objectives.",
    "Additionally, big data analytics has enabled organizations to process and analyze information at an unprecedented scale and velocity that was previously unimaginable even a decade ago. Advanced visualization tools transform complex datasets into intuitive dashboards and reports, making actionable insights accessible to decision-makers at all levels of the organization efficiently and effectively. This democratization of data has fostered a culture of evidence-based decision-making throughout enterprises, replacing intuition-driven approaches with rigorous analytical methodologies that deliver measurable, consistent results and enable organizations to identify emerging opportunities before their competitors do.",
    "The adoption of remote work technologies has also accelerated dramatically in recent years, fundamentally changing how teams collaborate and communicate across geographical boundaries and time zones. Video conferencing platforms, project management tools, and cloud-based document sharing systems have become essential infrastructure for modern organizations seeking to maintain productivity while embracing flexible work arrangements that accommodate diverse employee needs and preferences in an increasingly global workforce.",
    "Sustainable technology initiatives are gaining significant momentum as organizations recognize the importance of reducing their environmental footprint while maintaining operational efficiency and profitability. Green computing practices, energy-efficient data centers, and carbon-neutral cloud services are becoming standard offerings from major technology providers, enabling businesses to align their digital transformation strategies with environmental responsibility goals and meet growing stakeholder expectations for corporate sustainability.",
    "The convergence of augmented reality and virtual reality technologies is creating unprecedented opportunities for immersive customer experiences and enhanced employee training programs across multiple industries. These cutting-edge technologies enable organizations to create virtual showrooms where customers can interact with products in three-dimensional space, conduct remote equipment maintenance through augmented overlays that guide technicians step-by-step, and deliver interactive educational content that significantly improves engagement and knowledge retention rates compared to traditional learning methods.",
    "Furthermore, the development of quantum computing promises to revolutionize computational capabilities by solving complex optimization problems that are currently intractable for classical computers within any reasonable timeframe. While still in its early stages of practical application, quantum computing research is advancing rapidly with major technology companies investing billions of dollars to develop practical quantum systems for commercial applications in cryptography, drug discovery, financial modeling, and logistics optimization that could fundamentally transform entire industries.",
    "The growing importance of data governance and privacy regulations has compelled organizations worldwide to implement robust data management frameworks that ensure regulatory compliance while maximizing the strategic value of their information assets. Regulations such as the General Data Protection Regulation and the California Consumer Privacy Act have established new standards for data protection, requiring businesses to adopt transparent data practices, implement comprehensive security measures, and provide individuals with greater control over their personal information in an era where data has become one of the most valuable corporate assets.",
    "Edge computing architectures are increasingly being adopted to address the latency and bandwidth challenges associated with processing massive volumes of data generated by Internet of Things devices in real-time environments. By moving computation closer to the data source rather than relying solely on centralized cloud infrastructure, organizations can achieve faster response times, reduce network congestion, and maintain operational continuity even in scenarios with intermittent connectivity, making edge computing particularly valuable for applications in autonomous vehicles, industrial automation, and smart city infrastructure.",
    "Natural language processing technologies have advanced dramatically, enabling organizations to extract meaningful insights from unstructured text data at scale while improving customer interactions through intelligent chatbots and virtual assistants. These AI-powered systems can now understand context, sentiment, and intent with remarkable accuracy, allowing businesses to automate customer support workflows, analyze social media feedback, and generate comprehensive reports from large volumes of textual information without requiring extensive human intervention or manual review processes.",
    "The evolution of 5G wireless networks is unlocking new possibilities for real-time applications that demand ultra-low latency and massive device connectivity across diverse environments and use cases. Organizations are leveraging 5G capabilities to deploy advanced IoT solutions, enable remote surgery in healthcare settings, support autonomous vehicle communications, and create immersive augmented reality experiences that were previously impossible with older wireless technologies, fundamentally expanding the scope of what digital transformation can achieve.",
]

# Combine to get 2000+ words
paras = paras_base + paras_extra + paras_base[:4]  # Add some repeat to hit 2000+

text = " ".join(paras)
input_words = len(text.split())
print(f"=== TEST: {input_words} words ===")
print()

ZG_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin": "https://www.zerogpt.com",
    "Referer": "https://www.zerogpt.com/",
}

def zerogpt_check(txt):
    payload = json.dumps({"input_text": txt}).encode()
    req = urllib.request.Request(
        "https://api.zerogpt.com/api/detect/detectText",
        data=payload, headers=ZG_HEADERS
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = json.loads(resp.read())
    if raw.get("data") is None:
        print(f"  [WARN] ZeroGPT raw response: {json.dumps(raw)[:300]}")
        return {"feedback": "ERROR", "fakePercentage": -1, "h": [], "textWords": 0, "aiWords": 0}
    return raw.get("data", {})

# Step 1: Test RAW AI text
print("[1] Testing RAW AI text on ZeroGPT...")
r1 = zerogpt_check(text)
print(f"  Result: {r1.get('feedback')}")
print(f"  AI%: {r1.get('fakePercentage')}%")
print()

# Step 2: Humanize
print("[2] Humanizing (2 passes, gpt-5.4-mini)...")
t0 = time.time()
payload2 = json.dumps({"text": text, "passes": 2, "model": "cx/gpt-5.4-mini"}).encode()
req2 = urllib.request.Request("http://localhost:7860/api/humanize", data=payload2, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req2, timeout=600) as resp2:
    data = json.loads(resp2.read())
elapsed = time.time() - t0
humanized = data["result"]
out_words = data["output_words"]
print(f"  Output: {out_words} words ({round(out_words/input_words*100)}%)")
print(f"  Time: {elapsed:.1f}s")
in_s = data.get("input_score", {})
out_s = data.get("output_score", {})
print(f"  Our Score: {in_s.get('score')} ({in_s.get('grade')}) -> {out_s.get('score')} ({out_s.get('grade')})")
print(f"  Burstiness: {in_s.get('burstiness',{}).get('cv')} -> {out_s.get('burstiness',{}).get('cv')}")
print()

# Step 3: Test humanized text
print("[3] Testing HUMANIZED text on ZeroGPT...")
r2 = zerogpt_check(humanized)
print(f"  Result: {r2.get('feedback')}")
print(f"  AI%: {r2.get('fakePercentage')}%")
print(f"  AI sentences: {len(r2.get('h', []))}")
print()

# Summary
print("=" * 55)
print(f"SUMMARY ({input_words} -> {out_words} words):")
print(f"  BEFORE: {r1.get('fakePercentage')}% AI - {r1.get('feedback')}")
print(f"  AFTER:  {r2.get('fakePercentage')}% AI - {r2.get('feedback')}")
pct_before = r1.get("fakePercentage", 100)
pct_after = r2.get("fakePercentage", 100)
print(f"  Reduction: {round(pct_before - pct_after, 1)} percentage points")
verdict = "PASS" if pct_after < 25 else "FAIL"
print(f"  VERDICT: {verdict}")
print("=" * 55)
