import { describe, expect, it } from "vitest";
import {
  createBulkDownloadPlan,
  deleteSelectedFilePaths,
} from "./fileBulkActions";

describe("FileNav bulk actions", () => {
  it("keeps a lone file on the direct-download path", () => {
    expect(createBulkDownloadPlan(["/reports/q1.csv"])).toEqual({
      kind: "file",
      path: "/reports/q1.csv",
    });
  });

  it("archives directories and multiple selected items with normalized paths", () => {
    expect(
      createBulkDownloadPlan(["/reports/monthly/", "/reports/q1.csv"]),
    ).toEqual({
      kind: "archive",
      paths: ["/reports/monthly", "/reports/q1.csv"],
    });
    expect(createBulkDownloadPlan(["/reports/monthly/"])).toEqual({
      kind: "archive",
      paths: ["/reports/monthly"],
    });
  });

  it("does not start a download for an empty selection", () => {
    expect(createBulkDownloadPlan([])).toBeNull();
  });

  it("deletes all selected paths in order and reports partial success", async () => {
    const attempted: string[] = [];
    const result = await deleteSelectedFilePaths(
      ["/reports/monthly/", "/missing.csv"],
      async (path) => {
        attempted.push(path);
        return path !== "/missing.csv";
      },
    );

    expect(attempted).toEqual(["/reports/monthly", "/missing.csv"]);
    expect(result).toEqual({ deleted: 1, total: 2 });
  });
});
