const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktopPet", {
  onAction(callback) {
    ipcRenderer.on("pet:action", (_event, payload) => callback(payload));
  },
  reportState(state) {
    return ipcRenderer.invoke("pet:report-state", state);
  },
  setClickThrough(enabled) {
    return ipcRenderer.invoke("pet:set-click-through", enabled);
  },
});
