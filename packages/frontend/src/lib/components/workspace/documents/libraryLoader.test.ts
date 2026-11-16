import { describe, expect, it, vi } from "vitest";
import { createLibraryLoader } from "./libraryLoader";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function setup() {
  const documents = [{ id: "a" }, { id: "b" }];
  let selectedId: string | null = "a";
  const apply = vi.fn(
    (_documents: { id: string }[], selected: { id: string } | null) => {
      selectedId = selected?.id ?? null;
    },
  );
  const onError = vi.fn();
  const onSettled = vi.fn();
  const loadDocuments = vi.fn(async () => documents);
  const loadPreviews = vi.fn(async (id: string) => [id]);
  const loader = createLibraryLoader({
    selectedId: () => selectedId,
    loadDocuments,
    loadPreviews,
    apply,
    onError,
    onSettled,
  });
  return {
    loader,
    apply,
    onError,
    onSettled,
    loadDocuments,
    loadPreviews,
    select: (id: string | null) => {
      selectedId = id;
    },
  };
}

describe("document library refresh", () => {
  it("discards an old preview response after switching documents", async () => {
    const state = setup();
    const old = deferred<string[]>();
    state.loadPreviews.mockImplementationOnce(() => old.promise);
    const first = state.loader.refresh();
    await vi.waitFor(() => expect(state.loadPreviews).toHaveBeenCalledOnce());
    state.select("b");
    await state.loader.refresh();
    old.resolve(["preview for a"]);
    await first;
    expect(state.apply).toHaveBeenCalledTimes(1);
    expect(state.apply.mock.calls[0].slice(1)).toEqual([{ id: "b" }, ["b"]]);
  });

  it("does not overlap background polling with pending work", async () => {
    const state = setup();
    const response = deferred<string[]>();
    state.loadPreviews.mockImplementationOnce(() => response.promise);
    const first = state.loader.refresh();
    await state.loader.refresh(true);
    expect(state.loadDocuments).toHaveBeenCalledTimes(1);
    response.resolve([]);
    await first;
  });

  it("clears selection and previews when the document disappears", async () => {
    const state = setup();
    state.loadDocuments.mockResolvedValue([]);
    await state.loader.refresh();
    expect(state.apply).toHaveBeenCalledWith([], null, []);
    expect(state.onSettled).toHaveBeenCalledOnce();
    expect(state.loadPreviews).not.toHaveBeenCalled();
  });

  it("ignores failures from a previous selection", async () => {
    const state = setup();
    const response = deferred<string[]>();
    state.loadPreviews.mockImplementationOnce(() => response.promise);
    const first = state.loader.refresh();
    await vi.waitFor(() => expect(state.loadPreviews).toHaveBeenCalledOnce());
    state.select("b");
    response.reject(new Error("stale failure"));
    await first;
    expect(state.onError).not.toHaveBeenCalled();
  });

  it("settles current failures so the loading state can recover", async () => {
    const state = setup();
    state.loadDocuments.mockRejectedValue(new Error("offline"));
    await state.loader.refresh();
    expect(state.onError).toHaveBeenCalledOnce();
    expect(state.onSettled).toHaveBeenCalledOnce();
  });

  it("does not update a page after disposal", async () => {
    const state = setup();
    const response = deferred<string[]>();
    state.loadPreviews.mockImplementationOnce(() => response.promise);
    const first = state.loader.refresh();
    await vi.waitFor(() => expect(state.loadPreviews).toHaveBeenCalledOnce());
    state.loader.dispose();
    response.resolve(["a"]);
    await first;
    await state.loader.refresh();
    expect(state.apply).not.toHaveBeenCalled();
    expect(state.onSettled).not.toHaveBeenCalled();
  });
});
