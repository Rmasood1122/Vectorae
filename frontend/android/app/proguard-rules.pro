# proguard-rules.pro — Vectorae
#
# ProGuard / R8 rules for release builds.
# For a Capacitor app, the JavaScript app logic is in the assets/ WebView and
# is NOT affected by ProGuard. Only the native Java/Kotlin bridge is minified.
# These rules ensure the bridge, Firebase, and Capacitor plugin classes survive.
#
# TODO: if you add native Java/Kotlin plugins or modules, add their keep rules here.

# ── Capacitor core ────────────────────────────────────────────────────────────
-keep class com.getcapacitor.** { *; }
-keep @com.getcapacitor.annotation.CapacitorPlugin class * { *; }
-dontwarn com.getcapacitor.**

# ── Our app package ───────────────────────────────────────────────────────────
-keep class com.clienthunter.app.** { *; }

# ── Firebase / Google Play Services ──────────────────────────────────────────
-keep class com.google.firebase.** { *; }
-keep class com.google.android.gms.** { *; }
-dontwarn com.google.firebase.**
-dontwarn com.google.android.gms.**

# ── Standard Android / AndroidX ───────────────────────────────────────────────
-keepattributes *Annotation*
-keepattributes SourceFile,LineNumberTable    # keeps line numbers in crash reports
-keep public class * extends java.lang.Exception

# ── Cordova plugin compatibility (Capacitor uses the Cordova plugin layer) ────
-keep class org.apache.cordova.** { *; }
-dontwarn org.apache.cordova.**

# ── Suppress warnings for missing classes that are only used at runtime ───────
-dontwarn javax.annotation.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
