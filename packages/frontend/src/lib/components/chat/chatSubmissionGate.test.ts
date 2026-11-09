import { describe, expect, it, vi } from "vitest";
import {
  evaluateChatSubmission,
  type ChatSubmissionGateOptions,
} from "./chatSubmissionGate";

describe("evaluateChatSubmission", () => {
  const createOptions = (
    overrides: Partial<ChatSubmissionGateOptions> = {},
  ) => ({
    pendingOAuthIntegration: false,
    prompt: "Summarize this report",
    currentFileCount: 0,
    hasUnselectedModel: false,
    getVariables: () => ({ conflicts: 0, missing: false, empty: false }),
    maximumFileCount: 5,
    chatFileCount: 0,
    needsWebSearchConfirmation: false,
    ...overrides,
  });

  it("stops before computing chat variables when an integration is still required", () => {
    const getVariables = vi.fn(() => ({
      conflicts: 0,
      missing: false,
      empty: false,
    }));

    expect(
      evaluateChatSubmission(
        createOptions({
          pendingOAuthIntegration: true,
          getVariables,
          prompt: "",
        }),
      ),
    ).toEqual({ status: "rejected", reason: "integration-required" });
    expect(getVariables).not.toHaveBeenCalled();
  });

  it("requires text or a current-turn file, even when chat files are already attached", () => {
    expect(
      evaluateChatSubmission(createOptions({ prompt: "", chatFileCount: 1 })),
    ).toEqual({ status: "rejected", reason: "prompt-required" });
  });

  it("allows an attachment-only message when the file is attached to this turn", () => {
    expect(
      evaluateChatSubmission(
        createOptions({ prompt: "", currentFileCount: 1 }),
      ),
    ).toEqual({ status: "ready" });
  });

  it("checks model selection before evaluating chat-variable requirements", () => {
    const getVariables = vi.fn(() => ({
      conflicts: 0,
      missing: true,
      empty: false,
    }));

    expect(
      evaluateChatSubmission(
        createOptions({ hasUnselectedModel: true, getVariables }),
      ),
    ).toEqual({ status: "rejected", reason: "model-not-selected" });
    expect(getVariables).not.toHaveBeenCalled();
  });

  it("rejects conflicting chat-variable definitions", () => {
    expect(
      evaluateChatSubmission(
        createOptions({
          getVariables: () => ({ conflicts: 2, missing: false, empty: false }),
        }),
      ),
    ).toEqual({ status: "rejected", reason: "chat-variables-conflict" });
  });

  it("defers when chat variables still need user input", () => {
    expect(
      evaluateChatSubmission(
        createOptions({
          getVariables: () => ({ conflicts: 0, missing: true, empty: false }),
        }),
      ),
    ).toEqual({ status: "deferred", reason: "chat-variables-input" });
  });

  it("allows the configured file-count boundary and rejects only counts above it", () => {
    expect(
      evaluateChatSubmission(
        createOptions({
          maximumFileCount: 3,
          currentFileCount: 1,
          chatFileCount: 2,
        }),
      ),
    ).toEqual({ status: "ready" });
    expect(
      evaluateChatSubmission(
        createOptions({
          maximumFileCount: 3,
          currentFileCount: 2,
          chatFileCount: 2,
        }),
      ),
    ).toEqual({ status: "rejected", reason: "file-limit", maximum: 3 });
  });

  it("defers for web-search confirmation after earlier checks pass", () => {
    expect(
      evaluateChatSubmission(
        createOptions({ needsWebSearchConfirmation: true }),
      ),
    ).toEqual({ status: "deferred", reason: "web-search-confirmation" });
  });

  it("allows a valid submission when no confirmation is needed", () => {
    expect(evaluateChatSubmission(createOptions())).toEqual({
      status: "ready",
    });
  });
});
