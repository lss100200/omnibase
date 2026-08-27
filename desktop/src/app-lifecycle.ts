export interface SingleInstanceApp {
  requestSingleInstanceLock(): boolean;
  quit(): void;
  on(event: "second-instance", listener: () => void): void;
}

export interface FocusableWindow {
  isMinimized(): boolean;
  restore(): void;
  focus(): void;
}

export function enforceSingleInstance(
  app: SingleInstanceApp,
  getWindow: () => FocusableWindow | null,
): boolean {
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return false;
  }
  app.on("second-instance", () => {
    const window = getWindow();
    if (window === null) {
      return;
    }
    if (window.isMinimized()) {
      window.restore();
    }
    window.focus();
  });
  return true;
}
