// Dummy login: invalid user ids are rejected in the browser before any request is sent.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Login } from "../components/Login";

afterEach(cleanup);

describe("Login", () => {
  it("rejects ids with spaces or symbols without calling the server", () => {
    const onSignIn = vi.fn().mockResolvedValue(undefined);
    render(<Login onSignIn={onSignIn} />);
    fireEvent.change(screen.getByLabelText("User id"), { target: { value: "bad user!" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("alert").textContent).toMatch(/Use 1 to 64/);
    expect(onSignIn).not.toHaveBeenCalled();
  });

  it("passes a valid id through trimmed", async () => {
    const onSignIn = vi.fn().mockResolvedValue(undefined);
    render(<Login onSignIn={onSignIn} />);
    fireEvent.change(screen.getByLabelText("User id"), { target: { value: "  analyst-01 " } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(onSignIn).toHaveBeenCalledWith("analyst-01"));
  });
});
