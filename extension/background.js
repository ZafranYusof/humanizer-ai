// HumanizeAI Chrome Extension - Background Service Worker
const API_URL = 'http://localhost:7860';

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'humanize-selection',
    title: 'Humanize with AI',
    contexts: ['selection']
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === 'humanize-selection' && info.selectionText) {
    humanizeText(info.selectionText, tab.id);
  }
});

async function humanizeText(text, tabId) {
  try {
    // Submit job
    const resp = await fetch(`${API_URL}/api/humanize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: text,
        passes: 3,
        tone: 'casual',
        model: 'QW/qwen3.6-flash'
      })
    });
    const data = await resp.json();
    const jobId = data.job_id;

    // Poll for result
    let result = null;
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 2000));
      const progResp = await fetch(`${API_URL}/api/progress/${jobId}`);
      const prog = await progResp.json();
      
      if (prog.status === 'done') {
        result = prog.result || prog.partial;
        break;
      }
    }

    if (result) {
      // Send result back to content script
      chrome.tabs.sendMessage(tabId, {
        action: 'humanized',
        original: text,
        humanized: result
      });
    }
  } catch (e) {
    console.error('HumanizeAI error:', e);
    chrome.tabs.sendMessage(tabId, {
      action: 'error',
      message: e.message
    });
  }
}
