import { describe, expect, it, vi } from "vitest";
import type { ChatResourceManifestDependencies } from "./chatResourceManifest";
import { buildChatRequestFiles } from "./chatResourceManifest";

type Dependencies = ChatResourceManifestDependencies;

const createHarness = (overrides: Partial<Dependencies> = {}) => {
  const cloneTrackedFiles = vi.fn((files: Dependencies["chatFiles"]) =>
    structuredClone(files),
  );
  const dependencies: Dependencies = {
    chatFiles: [],
    messages: [],
    userFiles: [],
    cloneTrackedFiles,
    isRasterImageContentType: (contentType) =>
      contentType?.startsWith("image/") ?? false,
    equal: (left, right) => JSON.stringify(left) === JSON.stringify(right),
    ...overrides,
  };
  return { result: buildChatRequestFiles(dependencies), cloneTrackedFiles };
};

describe("chat resource manifest preparation", () => {
  it("keeps only history-referenced chat files and excludes image-only attachments", () => {
    const doc = { id: "doc-1", type: "doc", name: "project brief" };
    const note = { id: "note-1", type: "note", name: "meeting notes" };
    const stale = { id: "stale-1", type: "doc", name: "removed file" };
    const currentUserDoc = { id: "doc-1", type: "doc", name: "project brief" };
    const text = { id: "text-1", type: "text", name: "readme.txt" };
    const pdf = { id: "pdf-1", type: "file", content_type: "application/pdf" };
    const image = { id: "image-1", type: "image", content_type: "image/png" };
    const rasterFile = {
      id: "image-2",
      type: "file",
      content_type: "image/jpeg",
    };
    const cloneTrackedFiles = vi.fn((files: Dependencies["chatFiles"]) =>
      structuredClone(files),
    );
    const dependencies: Dependencies = {
      chatFiles: [doc, note, stale],
      messages: [{ files: [{ id: "doc-1" }] }, { files: [{ id: "note-1" }] }],
      userFiles: [currentUserDoc, text, pdf, image, rasterFile],
      cloneTrackedFiles,
      isRasterImageContentType: (contentType) =>
        contentType?.startsWith("image/") ?? false,
      equal: (left, right) => JSON.stringify(left) === JSON.stringify(right),
    };

    const result = buildChatRequestFiles(dependencies);

    expect(result.chatFiles).toEqual([doc, note]);
    expect(result.files).toEqual([doc, note, text, pdf]);
    expect(result.files[0]).not.toBe(doc);
    expect(cloneTrackedFiles).toHaveBeenCalledWith([doc, note]);
  });

  it("preserves distinct references while deduplicating equal file entries", () => {
    const trackedFile = { id: "same-id", type: "doc", name: "file" };
    const userFile = { id: "same-id", type: "doc", name: "file" };
    const { result } = createHarness({
      chatFiles: [trackedFile],
      messages: [{ files: [{ id: "same-id" }] }],
      userFiles: [userFile],
    });

    expect(result.files).toEqual([trackedFile]);
  });
});
