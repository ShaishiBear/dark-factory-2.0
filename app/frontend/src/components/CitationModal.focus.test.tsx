/**
 * Acceptance tests for issue #189: keyboard focus management on CitationModal.
 *
 * AC-1 (red): When CitationModal mounts, keyboard focus moves off the
 *   previously focused element and onto the modal's Close button.
 *
 * AC-2 (red): When CitationModal closes (any path), keyboard focus is
 *   restored to the element that was focused immediately before the
 *   modal opened, when that element remains in the document.
 *
 * AC-4 (guard): When the previously focused element has been removed
 *   from the document before close, closing completes without throwing
 *   and focus falls back to body (not a detached node).
 *
 * RED fragments pinned per assertion via the second arg to expect(...),
 * which vitest echoes after the standard chai comparison output.
 */

import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Citation } from '../lib/api';
import { CitationModal } from './CitationModal';

const mockCitation: Citation = {
  chunk_id: 'chunk-1',
  video_id: 'vid-1',
  video_title: 'Test Video Title',
  video_url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
  start_seconds: 754,
  end_seconds: 762,
  snippet: 'snippet text',
};

describe('CitationModal — focus management (issue #189)', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('AC-1: moves keyboard focus to the Close button when the modal mounts', () => {
    // Some element in the document has keyboard focus.
    const opener = document.createElement('button');
    opener.textContent = 'opener';
    document.body.appendChild(opener);
    opener.focus();
    expect(document.activeElement).toBe(opener);

    // Mount the modal. The conforming implementation moves focus to the
    // Close button (role=button, accessible name 'Close') inside the
    // role='dialog' element labelled 'Video citation'.
    const onClose = vi.fn();
    render(<CitationModal citation={mockCitation} onClose={onClose} />);

    const closeButton = screen.getByRole('button', { name: 'Close' });
    expect(document.activeElement, 'AC-1: focus must move to Close button on mount').toBe(
      closeButton,
    );
  });

  it('AC-2: restores keyboard focus to the previously focused element on close', () => {
    // Element with keyboard focus before the modal opens.
    const opener = document.createElement('button');
    opener.textContent = 'opener';
    document.body.appendChild(opener);
    opener.focus();

    const onClose = vi.fn();
    const { unmount } = render(<CitationModal citation={mockCitation} onClose={onClose} />);

    // Simulate the mount-time focus move so the cleanup path has a known
    // state to restore from. The mount-time move is exercised by AC-1;
    // here we isolate the cleanup assertion.
    const closeButton = screen.getByRole('button', { name: 'Close' });
    closeButton.focus();
    expect(document.activeElement).toBe(closeButton);

    // Close the modal by unmounting — the cleanup path that should
    // restore focus to the opener runs here.
    unmount();

    expect(document.activeElement, 'AC-2: focus must return to opener on unmount').toBe(opener);
  });

  it('AC-4: closes safely without throwing when the opener is removed before close', () => {
    // Opener with keyboard focus before the modal opens.
    const opener = document.createElement('button');
    opener.textContent = 'opener';
    document.body.appendChild(opener);
    opener.focus();

    const onClose = vi.fn();
    const { unmount } = render(<CitationModal citation={mockCitation} onClose={onClose} />);

    // Establish close-button focus so the cleanup has a known source state.
    const closeButton = screen.getByRole('button', { name: 'Close' });
    closeButton.focus();

    // Streaming re-render / message removal: the opener is detached while
    // the modal is still open. The cleanup must not throw and must not
    // call focus on the detached node.
    opener.remove();

    expect(() => unmount()).not.toThrow();
    expect(document.activeElement).toBe(document.body);
  });
});
