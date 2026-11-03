/** Opening an admin link while signed out: sign in, land back on that page. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));

const authState: { user: unknown; isLoading: boolean } = { user: null, isLoading: false };
vi.mock("@/lib/stores/authStore", () => ({ useAuthStore: () => authState }));
vi.mock("@/lib/api/admin", () => ({
  adminApi: { growthAccess: vi.fn().mockRejectedValue(new Error("403")) },
}));
vi.mock("@/components/admin/ConfigWarningsBanner", () => ({ ConfigWarningsBanner: () => null }));

const login = vi.fn();
vi.mock("@/lib/api/auth", () => ({
  login: (...a: unknown[]) => login(...a),
  me: vi.fn(),
  isUnverifiedError: () => false,
}));
vi.mock("@/lib/api/client", async (orig) => {
  const real = await orig<typeof import("@/lib/api/client")>();
  return { ...real, restoreSession: vi.fn().mockResolvedValue(false) };
});

import AdminLayout from "@/app/(admin)/admin/layout";
import LoginPage from "@/app/(auth)/login/page";

const VERIFIED_ADMIN = {
  email: "vectorae@calendarharvest.com", email_verified: true, identity_required: false,
  identity_complete: true, phone_verified: true, has_active_plan: true, is_admin: true,
};

function renderWithQuery(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  replace.mockReset();
  login.mockReset();
  window.sessionStorage.clear();
});
afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("signed-out visit to an admin page", () => {
  it("goes to sign-in and remembers the page", async () => {
    authState.user = null;
    window.history.replaceState(null, "", "/admin/growth?days=7");
    renderWithQuery(<AdminLayout><div>x</div></AdminLayout>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(window.sessionStorage.getItem("lp_return_to")).toBe("/admin/growth?days=7");
  });

  it("a signed-in non-admin is still sent away and nothing is remembered", async () => {
    authState.user = { ...VERIFIED_ADMIN, is_admin: false };
    window.history.replaceState(null, "", "/admin/growth");
    renderWithQuery(<AdminLayout><div>x</div></AdminLayout>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(window.sessionStorage.getItem("lp_return_to")).toBeNull();
  });
});

describe("sign-in", () => {
  async function signIn() {
    window.history.replaceState(null, "", "/login");
    render(<LoginPage />);
    fireEvent.change(await screen.findByLabelText(/email/i),
                     { target: { value: VERIFIED_ADMIN.email } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: "Sup3rSecret!" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
  }

  it("returns to the remembered admin page", async () => {
    window.sessionStorage.setItem("lp_return_to", "/admin/growth");
    login.mockResolvedValue(VERIFIED_ADMIN);
    await signIn();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/admin/growth"));
    expect(window.sessionStorage.getItem("lp_return_to")).toBeNull();
  });

  it("goes to the dashboard when nothing was remembered", async () => {
    login.mockResolvedValue(VERIFIED_ADMIN);
    await signIn();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/pipeline"));
  });

  it("an unverified account still goes to check-email first", async () => {
    window.sessionStorage.setItem("lp_return_to", "/admin/growth");
    login.mockResolvedValue({ ...VERIFIED_ADMIN, email_verified: false });
    await signIn();
    await waitFor(() => expect(replace).toHaveBeenCalledWith(
      expect.stringMatching(/^\/check-email/)));
  });
});
