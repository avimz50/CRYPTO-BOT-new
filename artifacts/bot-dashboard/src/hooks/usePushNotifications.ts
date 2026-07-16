import { useState, useEffect, useCallback } from "react";

const API_BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

export type PushState = "unsupported" | "loading" | "denied" | "subscribed" | "unsubscribed";

export function usePushNotifications() {
  const [state, setState] = useState<PushState>("loading");

  const checkStatus = useCallback(async () => {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      setState("unsupported");
      return;
    }
    if (Notification.permission === "denied") {
      setState("denied");
      return;
    }
    try {
      const reg = await navigator.serviceWorker.ready;
      const existing = await reg.pushManager.getSubscription();
      setState(existing ? "subscribed" : "unsubscribed");
    } catch {
      setState("unsubscribed");
    }
  }, []);

  useEffect(() => {
    checkStatus();
  }, [checkStatus]);

  const subscribe = useCallback(async (): Promise<boolean> => {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return false;
    try {
      const vapidRes = await fetch(`${API_BASE}/api/push/vapid-public-key`, { credentials: "include" });
      if (!vapidRes.ok) return false;
      const { publicKey } = await vapidRes.json() as { publicKey: string };

      const reg = await navigator.serviceWorker.ready;
      const subscription = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });

      const saveRes = await fetch(`${API_BASE}/api/push/subscribe`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(subscription),
      });

      if (saveRes.ok) {
        setState("subscribed");
        return true;
      }
      return false;
    } catch (e) {
      console.error("[Push] subscribe error:", e);
      if (Notification.permission === "denied") setState("denied");
      return false;
    }
  }, []);

  const unsubscribe = useCallback(async (): Promise<boolean> => {
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) await sub.unsubscribe();
      await fetch(`${API_BASE}/api/push/subscribe`, { method: "DELETE", credentials: "include" });
      setState("unsubscribed");
      return true;
    } catch {
      return false;
    }
  }, []);

  return { state, subscribe, unsubscribe };
}
