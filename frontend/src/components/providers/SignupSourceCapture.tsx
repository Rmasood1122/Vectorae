"use client";
import { useEffect } from "react";
import { captureSignupSource } from "@/lib/signupSource";

/** Records where this visit came from, once per tab (lib/signupSource.ts). */
export function SignupSourceCapture() {
  useEffect(() => { captureSignupSource(); }, []);
  return null;
}
