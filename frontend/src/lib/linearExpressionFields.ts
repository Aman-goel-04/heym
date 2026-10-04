export type LinearExpressionFieldKey =
  | "linearLimit"
  | "linearAfter"
  | "linearTeamId"
  | "linearProjectId"
  | "linearIssueId"
  | "linearTitle"
  | "linearDescription"
  | "linearStateId"
  | "linearIssueLinkUrl"
  | "linearAssigneeId"
  | "linearPriority"
  | "linearCommentId"
  | "linearCommentBody"
  | "linearParentCommentId";

export interface LinearExpressionField {
  key: LinearExpressionFieldKey;
  label: string;
}

export interface LinearExpressionFieldContext {
  returnAll?: boolean;
}

const listOperations = new Set([
  "listTeams",
  "listProjects",
  "listIssues",
  "listTeamMembers",
  "listComments",
]);

const teamOperations = new Set([
  "listIssues",
  "createIssue",
  "updateIssue",
  "listWorkflowStates",
  "listTeamMembers",
]);

const projectOperations = new Set([
  "listIssues",
  "createIssue",
  "updateIssue",
]);

const issueIdOperations = new Set([
  "getIssue",
  "updateIssue",
  "deleteIssue",
  "addIssueLink",
  "createComment",
  "listComments",
]);

const commentIdOperations = new Set([
  "updateComment",
  "deleteComment",
  "resolveComment",
  "unresolveComment",
]);

const issueDetailOperations = new Set([
  "createIssue",
  "updateIssue",
]);

const commentBodyOperations = new Set([
  "createComment",
  "updateComment",
]);

function appendPaginationFields(
  fields: LinearExpressionField[],
  context: LinearExpressionFieldContext,
): void {
  if (!context.returnAll) {
    fields.push({ key: "linearLimit", label: "Limit" });
    fields.push({ key: "linearAfter", label: "After Cursor" });
  }
}

/** Returns ordered expression-evaluate dialog slots for the given Linear operation. */
export function getLinearExpressionFields(
  operation: string,
  context: LinearExpressionFieldContext = {},
): LinearExpressionField[] {
  const op = operation || "listIssues";
  const fields: LinearExpressionField[] = [];

  if (listOperations.has(op)) {
    appendPaginationFields(fields, context);
  }

  if (teamOperations.has(op)) {
    fields.push({ key: "linearTeamId", label: "Team ID" });
  }

  if (projectOperations.has(op)) {
    fields.push({ key: "linearProjectId", label: "Project ID" });
  }

  if (issueIdOperations.has(op)) {
    fields.push({ key: "linearIssueId", label: "Issue ID or Identifier" });
  }

  if (commentIdOperations.has(op)) {
    fields.push({ key: "linearCommentId", label: "Comment ID" });
  }

  if (issueDetailOperations.has(op)) {
    fields.push({ key: "linearTitle", label: "Title" });
    fields.push({ key: "linearDescription", label: "Description" });
    fields.push({ key: "linearStateId", label: "State ID" });
    fields.push({ key: "linearAssigneeId", label: "Assignee ID" });
    fields.push({ key: "linearPriority", label: "Priority" });
  }

  if (op === "addIssueLink") {
    fields.push({ key: "linearIssueLinkUrl", label: "Link URL" });
  }

  if (commentBodyOperations.has(op)) {
    fields.push({ key: "linearCommentBody", label: "Comment Body" });
  }

  if (op === "createComment") {
    fields.push({ key: "linearParentCommentId", label: "Parent Comment ID" });
  }

  return fields;
}
