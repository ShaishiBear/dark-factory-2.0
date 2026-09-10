/**
 * Acceptance test for issue #103: the first message's stream is aborted by
 * React.StrictMode's effect re-run, nothing renders.
 *
 * Renders the first-send flow — ChatArea at /c/<id> with
 * location.state.initialMessage, exactly as in the #205 regression test —
 * wrapped in React.StrictMode. The mocked fetch captures the AbortSignal
 * it receives on POST /api/conversations/<id>/messages and returns a
 * readable SSE body whose [DONE] is gated behind a Promise the test
 * releases, so the in-flight state is observable.
 *
 * Pinned RED assertion (issue #103 acceptance):
 *   expect(capturedSignal?.aborted).toBe(false)
 * On the unchanged tree, StrictMode's second pass of the
 * conversationId-reset effect in useStreamingResponse aborts
 * streamAbortRef.current and ChatArea's send path swallows the
 * AbortError as intentional. vitest prints
 *   "expected true to be false // Object.is equality"
 * and the test fails red. After the fix, the captured signal stays
 * live, the "Stop response" button is visible mid-flight, and the
 * streamed assistant text renders via the streaming bubble.
 */

import { render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatArea } from '../components/ChatArea';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => vi.fn(),
  };
});

vi.mock('../hooks/useMessages', () => ({
  useMessages: () => ({
    messages: [],
    setMessages: vi.fn(),
    loading: false,
    error: null,
    notFound: false,
    conversation: null,
  }),
}));

vi.mock('../hooks/useToast', () => ({
  useToast: () => ({
    addToast: vi.fn(),
    removeToast: vi.fn(),
  }),
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'test-user', email: 'test@test', is_admin: false },
    refresh: vi.fn(),
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
});

describe('ChatArea — first-send under React.StrictMode (issue #103)', () => {
  it('does not abort the first-send stream under StrictMode re-run', async () => {
    let capturedSignal: AbortSignal | undefined;
    let releaseDone: (() => void) | undefined;
    const doneGate = new Promise<void>((r) => {
      releaseDone = r;
    });

    const enc = new TextEncoder();
    const sseBody = new ReadableStream<Uint8Array>({
      async start(controller) {
        controller.enqueue(enc.encode('data: "Hello from stream"\n\n'));
        await doneGate;
        controller.enqueue(enc.encode('data: [DONE]\n\n'));
        controller.close();
      },
    });

    vi.spyOn(global, 'fetch').mockImplementation(((
      input: RequestInfo | URL,
      init?: RequestInit,
    ) => {
      const url = typeof input === 'string' ? input : (input as URL).href;
      if (url.includes('/messages')) {
        if (init?.signal instanceof AbortSignal) {
          capturedSignal = init.signal;
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          body: sseBody,
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        body: new ReadableStream({
          start(c) {
            c.close();
          },
        }),
      } as unknown as Response);
    }) as typeof fetch);

    render(
      <StrictMode>
        <MemoryRouter
          initialEntries={[
            { pathname: '/c/conv-1', state: { initialMessage: 'How do subagents work?' } },
          ]}
        >
          <ChatArea conversationId="conv-1" />
        </MemoryRouter>
      </StrictMode>,
    );

    // Wait until the dispatchedInitial effect's handleSend has wired
    // startStream to fetch — only then is the captured signal meaningful.
    await waitFor(() => {
      expect(capturedSignal).toBeDefined();
    });

    // Pinned RED: the captured AbortSignal must NOT be aborted by
    // StrictMode's second pass of the conversationId-reset effect.
    // On the broken tree this aborts streamAbortRef.current and ChatArea's
    // send path swallows the AbortError as intentional, yielding exactly
    // "expected true to be false // Object.is equality".
    expect(capturedSignal?.aborted).toBe(false);

    // After the fix: "Stop response" button is visible while the SSE stream
    // is in flight (streamingContent is held mid-stream by the doneGate).
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Stop response' })).toBeInTheDocument();
    });

    // After the fix: the streamed assistant text renders via the live
    // streaming bubble while isStreaming is true and streamingContent
    // carries the accumulated tokens.
    await waitFor(
      () => {
        expect(screen.getByText('Hello from stream')).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    // Release the gate so the [DONE] arrives and the reader can wind down.
    releaseDone?.();
  });
});
