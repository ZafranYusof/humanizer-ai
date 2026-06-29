// HumanizeAI Chrome Extension - Content Script

// Store original selection for replacement
let lastSelection = '';

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'humanized') {
    replaceSelectedText(msg.humanized);
    showNotification('Text humanized!');
  } else if (msg.action === 'error') {
    showNotification('Error: ' + msg.message, true);
  }
});

function replaceSelectedText(newText) {
  const sel = window.getSelection();
  if (!sel.rangeCount) return;
  
  const range = sel.getRangeAt(0);
  const el = range.startContainer.parentElement;
  
  // For contenteditable elements
  if (el.isContentEditable || el.closest('[contenteditable="true"]')) {
    range.deleteContents();
    range.insertNode(document.createTextNode(newText));
  }
  // For textarea/input
  else if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
    const start = el.selectionStart;
    const end = el.selectionEnd;
    el.value = el.value.substring(0, start) + newText + el.value.substring(end);
    el.selectionStart = start;
    el.selectionEnd = start + newText.length;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
  // For regular text - copy to clipboard
  else {
    navigator.clipboard.writeText(newText).then(() => {
      showNotification('Humanized text copied to clipboard!');
    });
  }
}

function showNotification(text, isError = false) {
  const div = document.createElement('div');
  div.style.cssText = `
    position: fixed; top: 20px; right: 20px; z-index: 999999;
    background: ${isError ? '#ff4444' : '#00cc88'}; color: #fff;
    padding: 12px 20px; border-radius: 8px; font-size: 14px;
    font-family: -apple-system, sans-serif; box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    transition: opacity 0.3s;
  `;
  div.textContent = text;
  document.body.appendChild(div);
  setTimeout(() => { div.style.opacity = '0'; }, 2500);
  setTimeout(() => div.remove(), 3000);
}
