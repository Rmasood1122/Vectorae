import type { CapacitorConfig } from '@capacitor/cli';

// ─── PERMANENT DECISION ──────────────────────────────────────────────────────
// appId CANNOT be changed after the first Play Store publish.
// ─────────────────────────────────────────────────────────────────────────────

const config: CapacitorConfig = {
  appId: 'com.clienthunter.app',
  appName: 'Vectorae',
  webDir: 'out',

  android: {
    // (androidScheme lives under `server` — it was duplicated here and is
    // not a valid key of the android block in Capacitor 6.)
  },

  server: {
    cleartext: false,
    androidScheme: 'https',
    // Dev hot-reload (never commit uncommenting):
    // url: 'http://10.0.2.2:3000',
  },

  plugins: {
    // ── Splash Screen ───────────────────────────────────────────────────────
    // launchAutoHide: false — we hide manually in NativeProvider (useSplashHide).
    // Prevents white flash between splash and first React paint.
    // TODO: verify SplashScreen plugin options against current Capacitor docs.
    SplashScreen: {
      launchShowDuration: 0,
      launchAutoHide: false,
      backgroundColor: '#0f172a',
      androidSplashResourceName: 'splash',
      androidScaleType: 'CENTER_CROP',
      showSpinner: false,
      splashFullScreen: true,
      splashImmersive: true,
    },

    // ── Push Notifications (Chunk 3) ───────────────────────────────────────
    // TODO: verify PushNotifications config keys for @capacitor/push-notifications
    PushNotifications: {
      presentationOptions: ['badge', 'sound', 'alert'],
    },
  },
};

export default config;
