/**
 * Acceptance test for issue #189, AC-3:
 * Activating a YouTube citation chip opens the citation modal and moves
 * keyboard focus inside the modal; pressing Escape closes the modal and
 * restores focus to the chip.
 *
 * RED fragment pinned per assertion via the second arg to expect(...),
 * which vitest echoes after the standard chai comparison output.
 *
 * jsdom focus semantics: fireEvent.click does not move focus, so the
 * test establishes focus with an explicit chip.focus() call before
 * activating the chip.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatArea } from '../components/ChatArea';
import type { Citation, Message } from '../lib/api';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

let mockMessages: Message[] = [];

vi.mock('../hooks/useMessages', () => ({
  useMessages: () => ({
    messages: mockMessages,
    setMessages: vi.fn(),
    loading: false,
    error: null,
    notFound: false,
    conversation: { id: 'conv-1', title: 'Test Chat', created_at: '', updated_at: '' },
  }),
}));

vi.mock('../hooks/useStreamingResponse', () => ({
  useStreamingResponse: () => ({
    streamingContent: '',
    streamingSources: [],
    isStreaming: false,
    startStream: vi.fn(),
    abortStream: vi.fn(),
  }),
}));

vi.mock('../hooks/useToast', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn() }),
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'test-user', email: 'test@test.com', is_admin: false },
    refresh: vi.fn(),
  }),
}));

const ytCitation: Citation = {
  chunk_id: 'c3',
  video_id: 'dQw4w9WgXcQ',
  video_title: 'YouTube Video Title',
  video_url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
  start_seconds: 60,
  end_seconds: 70,
  snippet: 'A snippet of text.',
  source_type: 'youtube',
  is_cited: true,
};

function makeAssistantMessage(citation: Citation): Message {
  return {
    id: 'msg-1',
    conversation_id: 'conv-1',
    role: 'assistant',
    content: 'Here is a source.',
    created_at: new Date().toISOString(),
    sources: [citation],
  };
}

describe('ChatArea — citation chip focus round-trip (AC-3, issue #189)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
    vi.stubGlobal('open', vi.fn());
    mockMessages = [];
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = '';
  });

  it('AC-3: opening the modal moves focus inside; Escape returns focus to the chip', async () => {
    mockMessages = [makeAssistantMessage(ytCitation)];

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    // Wait for the citation chip to render, then move keyboard focus to it.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /YouTube Video Title/i })).toBeInTheDocument();
    });
    const chip = screen.getByRole('button', {
      name: /YouTube Video Title/i,
    }) as HTMLButtonElement;
    chip.focus();
    expect(document.activeElement).toBe(chip);

    // Activate the chip — handleCitationClick opens the citation modal.
    fireEvent.click(chip);

    // Modal is open (YouTube iframe appears).
    await waitFor(() => {
      expect(screen.getByTitle('YouTube video player')).toBeInTheDocument();
    });

    expect(
      document.activeElement,
      'AC-3: focus must move off the chip into the modal on open',
    ).not.toBe(chip);

    // Close the modal with Escape (the existing document keydown handler).
    fireEvent.keyDown(document, { key: 'Escape' });

    // Modal is gone.
    await waitFor(() => {
      expect(screen.queryByTitle('YouTube video player')).not.toBeInTheDocument();
    });

    expect(
      document.activeElement,
      'AC-3: focus must return to chip after Escape closes modal',
    ).toBe(chip);
  });
});
