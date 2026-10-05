import { beforeEach, describe, expect, it, vi } from "vitest";

// `audio`/`playingId`/`currentUrl` in the source are module-level singletons,
// created once at import time (`typeof Audio !== "undefined" ? new Audio() : null`).
// That means:
//   1. Audio must be stubbed globally *before* the module is imported, or `audio`
//      binds to null forever. vi.hoisted() runs before the static import below.
//   2. All tests in this file share the same fake Audio instance and the same
//      playingId ref - reset both in beforeEach, or state leaks between tests.
const { FakeAudio, latestAudio } = vi.hoisted(() => {
  class FakeAudioImpl {
    src = "";
    currentTime = 0;
    onended: (() => void) | null = null;
    onerror: (() => void) | null = null;
    // Default: resolves immediately. Individual tests override this per call
    // with mockRejectedValueOnce()/mockImplementationOnce() for the stream-fail
    // and onerror-timing cases, which need full control over when/whether a
    // given play() call settles.
    play = vi.fn(async () => {});
    pause = vi.fn();
  }
  const holder: { current: FakeAudioImpl | null } = { current: null };
  class TrackedFakeAudio extends FakeAudioImpl {
    constructor() {
      super();
      holder.current = this;
    }
  }
  return { FakeAudio: TrackedFakeAudio, latestAudio: holder };
});

// A bare vi.stubGlobal() call is NOT auto-hoisted the way vi.mock()/vi.hoisted()
// are - only this call, wrapped in its own vi.hoisted(), runs before the static
// `import { useTextToSpeech } from "./useTextToSpeech"` below evaluates that
// module and reads `Audio` at its own top level. A previous version of this
// file called vi.stubGlobal("Audio", ...) as a bare statement here instead,
// which silently left `audio` bound to null for every test.
vi.hoisted(() => {
  vi.stubGlobal("Audio", FakeAudio);
});

// Incrementing rather than a fixed string, so a test can assert
// revokeObjectURL was called with the SPECIFIC url createObjectURL produced,
// not just "called with some string". URL itself doesn't need the same
// hoisting treatment as Audio: createObjectURL/revokeObjectURL are only read
// lazily inside speak()/stop() at call time, never at the source module's own
// top level, so stubbing it here in normal source order is already in time.
let objectUrlCounter = 0;
vi.stubGlobal("URL", {
  createObjectURL: vi.fn(() => `blob:${++objectUrlCounter}`),
  revokeObjectURL: vi.fn(),
});

vi.mock("@/services/api", () => ({
  voiceApi: {
    streamUrl: vi.fn(() => "https://heym.test/api/voice/tts/stream?text=x"),
    tts: vi.fn(async () => new Blob(["audio-bytes"])),
  },
}));

// Mutable, like `latestAudio` above: a vi.hoisted() holder object, so the
// vi.mock("@/stores/auth") factory below (itself hoisted above this file's
// imports) can close over something tests reassign later without hitting a
// temporal-dead-zone error on a plain `let`.
const authUserState = vi.hoisted(() => ({
  current: {
    tts_credential_id: "cred-1" as string | null,
    tts_voice_id: "voice-1" as string | null,
  },
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: vi.fn(() => ({
    get user() {
      return authUserState.current;
    },
  })),
}));

import { voiceApi } from "@/services/api";

import { useTextToSpeech } from "./useTextToSpeech";

const STREAM_URL = "https://heym.test/api/voice/tts/stream?text=x";
const LONG_URL = "x".repeat(6001); // > MAX_STREAM_URL_LENGTH (6000)

/** A promise plus its own resolve/reject, for tests that need to control
 * exactly when an awaited call settles (the race and onerror-timing cases). */
function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  // stop() first, while mocks still hold their call history, so its own
  // pause()/revokeObjectURL() side effects get wiped by clearAllMocks() right
  // after - otherwise every test would start already "contaminated" by one
  // extra pause() call from this cleanup.
  useTextToSpeech().stop();
  vi.clearAllMocks();
  if (latestAudio.current) {
    latestAudio.current.src = "";
  }
  authUserState.current = { tts_credential_id: "cred-1", tts_voice_id: "voice-1" };
  objectUrlCounter = 0;
  vi.mocked(voiceApi.streamUrl).mockReturnValue(STREAM_URL);
  vi.mocked(voiceApi.tts).mockResolvedValue(new Blob(["audio-bytes"]));
});

describe("isConfigured", () => {
  it("is true when both tts_credential_id and tts_voice_id are set", () => {
    authUserState.current = { tts_credential_id: "cred-1", tts_voice_id: "voice-1" };
    expect(useTextToSpeech().isConfigured.value).toBe(true);
  });

  it("is false when tts_credential_id is missing", () => {
    authUserState.current = { tts_credential_id: null, tts_voice_id: "voice-1" };
    expect(useTextToSpeech().isConfigured.value).toBe(false);
  });

  it("is false when tts_voice_id is missing", () => {
    authUserState.current = { tts_credential_id: "cred-1", tts_voice_id: null };
    expect(useTextToSpeech().isConfigured.value).toBe(false);
  });
});

describe("speak", () => {
  it("does nothing when the text is empty or whitespace-only", async () => {
    const { speak, playingId } = useTextToSpeech();

    await speak("a", "   ");

    expect(playingId.value).toBeNull();
    expect(latestAudio.current?.play).not.toHaveBeenCalled();
  });

  it("calling speak with the id that is already playing stops it instead of restarting it", async () => {
    const { speak, playingId } = useTextToSpeech();

    await speak("a", "hello");
    expect(playingId.value).toBe("a");

    vi.clearAllMocks();
    await speak("a", "hello");

    expect(latestAudio.current?.pause).toHaveBeenCalled();
    expect(playingId.value).toBeNull();
  });

  it("uses the streaming URL and sets playingId when the stream starts successfully (short text, play() resolves)", async () => {
    const { speak, playingId } = useTextToSpeech();

    await speak("a", "hello");

    expect(latestAudio.current?.src).toBe(STREAM_URL);
    expect(playingId.value).toBe("a");
    expect(voiceApi.tts).not.toHaveBeenCalled();
  });

  it("falls back to downloading the full clip when starting the stream throws (play() rejects)", async () => {
    latestAudio.current?.play.mockRejectedValueOnce(new Error("stream start failed"));
    const { speak, playingId } = useTextToSpeech();

    await speak("a", "hello");

    expect(voiceApi.tts).toHaveBeenCalledWith("hello");
    expect(latestAudio.current?.src).toBe("blob:1");
    expect(playingId.value).toBe("a");
  });

  it("skips streaming entirely and downloads directly when the stream URL exceeds MAX_STREAM_URL_LENGTH (long text)", async () => {
    vi.mocked(voiceApi.streamUrl).mockReturnValue(LONG_URL);
    const { speak, playingId } = useTextToSpeech();

    await speak("a", "hello");

    expect(voiceApi.tts).toHaveBeenCalledWith("hello");
    expect(latestAudio.current?.src).toBe("blob:1");
    expect(latestAudio.current?.src).not.toBe(LONG_URL);
    expect(playingId.value).toBe("a");
  });

  it("discards a download that resolves after a newer speak()/stop() call has already changed playingId (race: audio.src is never set to the stale blob)", async () => {
    // Force the download path deterministically, so this test only has to
    // reason about playDownloaded's own internal race guard, not the stream
    // attempt branch too.
    vi.mocked(voiceApi.streamUrl).mockReturnValue(LONG_URL);
    const ttsDeferred = deferred<Blob>();
    vi.mocked(voiceApi.tts).mockReturnValueOnce(ttsDeferred.promise);
    const { speak, stop, playingId } = useTextToSpeech();

    const speakPromise = speak("a", "hello");
    expect(playingId.value).toBe("a");

    // Something else happens before the download finishes: the user stops
    // playback, or starts a different clip. Either way playingId no longer
    // matches "a" by the time the tts() promise settles.
    stop();
    expect(playingId.value).toBeNull();

    ttsDeferred.resolve(new Blob(["late-audio"]));
    await speakPromise;

    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(latestAudio.current?.play).not.toHaveBeenCalled();
  });

  it("onended only resets playback state if playingId still matches the id that was playing when onended fires (not if a newer speak() call changed it first)", async () => {
    const { speak, playingId } = useTextToSpeech();

    await speak("a", "hello");
    const staleOnended = latestAudio.current?.onended;
    expect(staleOnended).toBeTypeOf("function");

    await speak("b", "world");
    expect(playingId.value).toBe("b");

    // A late popstate/onended event from the FIRST clip's closure, captured
    // before "b" ever started - it still remembers id "a" via closure.
    staleOnended?.();

    expect(playingId.value).toBe("b");
  });

  it("a stream-start error does not clear playback state (ignored while attempting the stream), but a *post-start* error does (onerror is rewired after play() resolves)", async () => {
    const playDeferred = deferred<void>();
    latestAudio.current?.play.mockImplementationOnce(() => playDeferred.promise);
    const { speak, playingId } = useTextToSpeech();

    const speakPromise = speak("a", "hello");
    await Promise.resolve(); // let the microtask queue reach `await audio.play()`

    // Still mid-attempt: onerror reads null here. Note this half is currently
    // guaranteed by stop()'s own `onerror = null` a few lines earlier in the
    // same speak() call, not by the stream branch's own (currently redundant,
    // given that call order) `audio.onerror = null` - confirmed by mutation-
    // testing this test: deleting that line alone did not make it fail. Kept
    // as a real assertion since the *outcome* (no stray handler fires here) is
    // still correct and worth protecting, even though it doesn't pin down
    // which line provides it.
    expect(latestAudio.current?.onerror).toBeNull();

    playDeferred.resolve();
    await speakPromise;

    // Playback started successfully: onerror is now the real mid-stream
    // failure handler.
    expect(latestAudio.current?.onerror).toBeTypeOf("function");
    latestAudio.current?.onerror?.();
    expect(playingId.value).toBeNull();
  });
});

describe("stop", () => {
  it("pauses the audio element and resets currentTime to 0", () => {
    if (latestAudio.current) latestAudio.current.currentTime = 42;
    const { stop } = useTextToSpeech();

    stop();

    expect(latestAudio.current?.pause).toHaveBeenCalled();
    expect(latestAudio.current?.currentTime).toBe(0);
  });

  it("clears onended/onerror so a late event from the old clip has no effect", async () => {
    const { speak, stop } = useTextToSpeech();
    await speak("a", "hello");
    expect(latestAudio.current?.onended).toBeTypeOf("function");

    stop();

    expect(latestAudio.current?.onended).toBeNull();
    expect(latestAudio.current?.onerror).toBeNull();
  });

  it("revokes the current object URL exactly once, with the URL it created", async () => {
    vi.mocked(voiceApi.streamUrl).mockReturnValue(LONG_URL);
    const { speak, stop } = useTextToSpeech();
    await speak("a", "hello");
    const createdUrl = latestAudio.current?.src;
    expect(createdUrl).toBe("blob:1");

    stop();

    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(createdUrl);
  });

  it("clears playingId", async () => {
    const { speak, stop, playingId } = useTextToSpeech();
    await speak("a", "hello");
    expect(playingId.value).toBe("a");

    stop();

    expect(playingId.value).toBeNull();
  });

  it("is a no-op (does not throw) when called while nothing is playing", () => {
    const { stop } = useTextToSpeech();

    expect(() => stop()).not.toThrow();
  });
});
