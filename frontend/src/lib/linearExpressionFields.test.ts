import { describe, expect, it } from "vitest";

import { getLinearExpressionFields } from "@/lib/linearExpressionFields";

describe("getLinearExpressionFields", () => {
  it("includes pagination and filter fields for listIssues by default", () => {
    const keys = getLinearExpressionFields("listIssues", { returnAll: false }).map(
      (field) => field.key,
    );

    expect(keys).toEqual([
      "linearLimit",
      "linearAfter",
      "linearTeamId",
      "linearProjectId",
    ]);
  });

  it("omits pagination fields when returnAll is enabled", () => {
    const keys = getLinearExpressionFields("listIssues", { returnAll: true }).map(
      (field) => field.key,
    );

    expect(keys).toEqual(["linearTeamId", "linearProjectId"]);
  });

  it("falls back to listIssues when operation is empty or undefined", () => {
    const defaultKeys = getLinearExpressionFields("").map((f) => f.key);
    expect(defaultKeys).toEqual([
      "linearLimit",
      "linearAfter",
      "linearTeamId",
      "linearProjectId",
    ]);

    const undefinedKeys = getLinearExpressionFields(undefined as unknown as string).map(
      (f) => f.key,
    );
    expect(undefinedKeys).toEqual(defaultKeys);
  });

  it("returns pagination fields for generic list operations", () => {
    expect(getLinearExpressionFields("listTeams").map((f) => f.key)).toEqual([
      "linearLimit",
      "linearAfter",
    ]);
    expect(getLinearExpressionFields("listProjects").map((f) => f.key)).toEqual([
      "linearLimit",
      "linearAfter",
    ]);
  });

  it("returns appropriate fields for workspace operations", () => {
    expect(getLinearExpressionFields("getViewer")).toEqual([]);
    expect(getLinearExpressionFields("listWorkflowStates").map((f) => f.key)).toEqual([
      "linearTeamId",
    ]);
    expect(getLinearExpressionFields("listTeamMembers").map((f) => f.key)).toEqual([
      "linearLimit",
      "linearAfter",
      "linearTeamId",
    ]);
  });

  it("includes issue mutation fields for createIssue with exact labels", () => {
    const fields = getLinearExpressionFields("createIssue");

    expect(fields).toEqual([
      { key: "linearTeamId", label: "Team ID" },
      { key: "linearProjectId", label: "Project ID" },
      { key: "linearTitle", label: "Title" },
      { key: "linearDescription", label: "Description" },
      { key: "linearStateId", label: "State ID" },
      { key: "linearAssigneeId", label: "Assignee ID" },
      { key: "linearPriority", label: "Priority" },
    ]);
  });

  it("includes issue ID and mutation fields for updateIssue", () => {
    const keys = getLinearExpressionFields("updateIssue").map((field) => field.key);

    expect(keys).toEqual([
      "linearTeamId",
      "linearProjectId",
      "linearIssueId",
      "linearTitle",
      "linearDescription",
      "linearStateId",
      "linearAssigneeId",
      "linearPriority",
    ]);
  });

  it("returns issue ID for getIssue and deleteIssue", () => {
    expect(getLinearExpressionFields("getIssue").map((f) => f.key)).toEqual(["linearIssueId"]);
    expect(getLinearExpressionFields("deleteIssue").map((f) => f.key)).toEqual(["linearIssueId"]);
  });

  it("includes link URL field for addIssueLink with exact labels", () => {
    const fields = getLinearExpressionFields("addIssueLink");

    expect(fields).toEqual([
      { key: "linearIssueId", label: "Issue ID or Identifier" },
      { key: "linearIssueLinkUrl", label: "Link URL" },
    ]);
  });

  it("returns pagination and issue ID for listComments", () => {
    const keys = getLinearExpressionFields("listComments").map((f) => f.key);

    expect(keys).toEqual(["linearLimit", "linearAfter", "linearIssueId"]);
  });

  it("includes comment fields for createComment", () => {
    const keys = getLinearExpressionFields("createComment").map((field) => field.key);

    expect(keys).toEqual([
      "linearIssueId",
      "linearCommentBody",
      "linearParentCommentId",
    ]);
  });

  it("includes comment ID and body for updateComment with exact labels", () => {
    const fields = getLinearExpressionFields("updateComment");

    expect(fields).toEqual([
      { key: "linearCommentId", label: "Comment ID" },
      { key: "linearCommentBody", label: "Comment Body" },
    ]);
  });

  it("returns comment ID for delete, resolve, and unresolve comment operations", () => {
    for (const op of ["deleteComment", "resolveComment", "unresolveComment"]) {
      const keys = getLinearExpressionFields(op).map((f) => f.key);
      expect(keys).toEqual(["linearCommentId"]);
    }
  });

  it("returns empty array for unknown operations", () => {
    expect(getLinearExpressionFields("unknownOperation")).toEqual([]);
  });
});
