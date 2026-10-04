import { expect, test } from "@playwright/test";

import { createWorkflow, deleteTeam, deleteWorkflow, expectOk, prepareAuthenticatedPage } from "./support";

interface TeamShareBody {
  team_id: string;
  permission: "read" | "write";
}

test("chooses read or write when sharing with a team and changes it later", async ({ page }) => {
  await prepareAuthenticatedPage(page);
  await page.setViewportSize({ width: 1600, height: 1000 });

  const teamName = `Share Perm Team ${Date.now()}`;
  const teamResponse = await page.request.post("/api/teams", { data: { name: teamName } });
  await expectOk(teamResponse);
  const team = (await teamResponse.json()) as { id: string };
  const workflow = await createWorkflow(page, `Share Perm ${Date.now()}`, [
    { id: "input", type: "textInput", position: { x: 0, y: 0 }, data: { label: "input" } },
  ]);

  const listShares = async (): Promise<TeamShareBody[]> => {
    const response = await page.request.get(`/api/workflows/${workflow.id}/team-shares`);
    await expectOk(response);
    return (await response.json()) as TeamShareBody[];
  };

  try {
    await page.goto(`/workflows/${workflow.id}`);
    await page.getByRole("button", { name: "Share", exact: true }).first().click();

    const dialog = page.getByTestId("workflow-share-dialog");
    await expect(dialog).toBeVisible();

    // The picker defaults to the least privilege, and the chosen permission reaches the API.
    const teamSelect = dialog.locator("select").nth(1);
    await teamSelect.selectOption({ label: teamName });
    const createResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/workflows/${workflow.id}/team-shares`) &&
        response.request().method() === "POST",
    );
    await page.getByTestId("workflow-share-team-add").click();
    const created = (await (await createResponse).json()) as TeamShareBody;
    expect(created.permission).toBe("read");

    const row = page.getByTestId(`workflow-team-share-row-${teamName}`);
    const rowSelect = page.getByTestId(`workflow-team-share-permission-${teamName}`).locator("select");
    await expect(rowSelect).toHaveValue("read");

    // An existing share can be switched to write without removing it first.
    const updateResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/workflows/${workflow.id}/team-shares`) &&
        response.request().method() === "POST",
    );
    await rowSelect.selectOption("write");
    await expectOk(await updateResponse);
    await expect(rowSelect).toHaveValue("write");
    await expect(row).toBeVisible();
    expect(await listShares()).toEqual([expect.objectContaining({ team_id: team.id, permission: "write" })]);

    // And back to read.
    const downgradeResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/workflows/${workflow.id}/team-shares`) &&
        response.request().method() === "POST",
    );
    await rowSelect.selectOption("read");
    await expectOk(await downgradeResponse);
    expect(await listShares()).toEqual([expect.objectContaining({ team_id: team.id, permission: "read" })]);
  } finally {
    await deleteWorkflow(page, workflow.id);
    await deleteTeam(page, team.id);
  }
});

test("asks whether to stop alerts when removing a share and sends the choice", async ({ page }) => {
  await prepareAuthenticatedPage(page);
  await page.setViewportSize({ width: 1600, height: 1000 });

  const teamName = `Revoke Team ${Date.now()}`;
  const teamResponse = await page.request.post("/api/teams", { data: { name: teamName } });
  await expectOk(teamResponse);
  const team = (await teamResponse.json()) as { id: string };
  const workflow = await createWorkflow(page, `Revoke ${Date.now()}`, [
    { id: "input", type: "textInput", position: { x: 0, y: 0 }, data: { label: "input" } },
  ]);

  const shareWithTeam = async (): Promise<void> => {
    await expectOk(
      await page.request.post(`/api/workflows/${workflow.id}/team-shares`, {
        data: { team_id: team.id, permission: "read" },
      }),
    );
  };

  const removeThroughDialog = async (stopAlerts: boolean): Promise<URL> => {
    await page.goto(`/workflows/${workflow.id}`);
    await page.getByRole("button", { name: "Share", exact: true }).first().click();
    await page.getByTestId(`workflow-team-share-remove-${teamName}`).click();

    const confirm = page.getByTestId("workflow-share-revoke-dialog");
    await expect(confirm).toBeVisible();
    const checkbox = page.getByTestId("workflow-share-revoke-stop-alerts");
    await expect(checkbox).toBeChecked();
    if (!stopAlerts) await checkbox.uncheck();

    const deleteRequest = page.waitForRequest(
      (request) =>
        request.url().includes(`/workflows/${workflow.id}/team-shares/${team.id}`) &&
        request.method() === "DELETE",
    );
    await page.getByTestId("workflow-share-revoke-confirm").click();
    const url = new URL((await deleteRequest).url());
    await expect(page.getByTestId(`workflow-team-share-row-${teamName}`)).toHaveCount(0);
    return url;
  };

  try {
    await shareWithTeam();
    expect((await removeThroughDialog(true)).searchParams.get("stop_alerts")).toBe("true");

    await shareWithTeam();
    expect((await removeThroughDialog(false)).searchParams.get("stop_alerts")).toBe("false");
  } finally {
    await deleteWorkflow(page, workflow.id);
    await deleteTeam(page, team.id);
  }
});

test("rejects an unknown permission and keeps legacy requests writable", async ({ page }) => {
  await prepareAuthenticatedPage(page);
  const teamResponse = await page.request.post("/api/teams", { data: { name: `Legacy Team ${Date.now()}` } });
  await expectOk(teamResponse);
  const team = (await teamResponse.json()) as { id: string };
  const workflow = await createWorkflow(page, `Legacy Share ${Date.now()}`);

  try {
    const invalid = await page.request.post(`/api/workflows/${workflow.id}/team-shares`, {
      data: { team_id: team.id, permission: "admin" },
    });
    expect(invalid.status()).toBe(422);

    // Clients that predate permissions send no field; they keep getting the old edit access.
    const legacy = await page.request.post(`/api/workflows/${workflow.id}/team-shares`, {
      data: { team_id: team.id },
    });
    await expectOk(legacy);
    expect(((await legacy.json()) as TeamShareBody).permission).toBe("write");
  } finally {
    await deleteWorkflow(page, workflow.id);
    await deleteTeam(page, team.id);
  }
});
