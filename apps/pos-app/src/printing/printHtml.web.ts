const CONTAINER_ID = 'slip-print';

/**
 * Print a slip through the browser's print dialog without leaving the app.
 *
 * Calling print() on a hidden frame is unreliable (some browsers open no dialog, others print the app page instead), so the
 * main window prints and a print-only stylesheet hides the whole app: only the slip frame reaches the paper. The slip stays in
 * its own frame, sandboxed without scripts, so its styles cannot touch the app and nothing in it can run.
 */
export function printHtml(html: string, widthMm: 58 | 80 = 80): Promise<void> {
  return new Promise((resolve, reject) => {
    document.getElementById(CONTAINER_ID)?.remove();

    const container = document.createElement('div');
    container.id = CONTAINER_ID;
    container.setAttribute('aria-hidden', 'true');
    const style = document.createElement('style');
    style.textContent = `
      @media screen { #${CONTAINER_ID} { position: fixed; left: -10000px; top: 0; } }
      @media print {
        @page { size: ${widthMm}mm auto; margin: 2mm; }
        html, body { margin: 0 !important; padding: 0 !important; height: auto !important; overflow: visible !important; background: #fff !important; }
        body > *:not(#${CONTAINER_ID}) { display: none !important; }
        #${CONTAINER_ID} { position: static; }
      }`;
    const frame = document.createElement('iframe');
    frame.setAttribute('sandbox', 'allow-same-origin');
    frame.setAttribute('scrolling', 'no');
    frame.style.cssText = `display: block; border: 0; width: ${widthMm - 4}mm; height: 0;`;
    container.append(style, frame);

    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      window.removeEventListener('afterprint', finish);
      setTimeout(() => container.remove(), 0);
      resolve();
    };

    frame.onload = () => {
      const doc = frame.contentDocument;
      if (!doc) {
        container.remove();
        reject(new Error('Printing is not available in this browser.'));
        return;
      }
      // The paper is as long as the slip: size the frame to its content so nothing is cut off or padded. The slip is laid out
      // to the frame's exact width, so sub-pixel rounding must not add scrollbars (they would print and clip the slip).
      doc.documentElement.style.overflow = 'hidden';
      frame.style.height = `${Math.ceil(doc.documentElement.scrollHeight) + 2}px`;
      window.addEventListener('afterprint', finish);
      window.print(); // blocks until the dialog closes in most browsers
      finish();
    };
    frame.srcdoc = html;
    document.body.appendChild(container);
  });
}
