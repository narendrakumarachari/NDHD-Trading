import React from "react";
import { html } from "../html.js";

export function TopBar({ account, connected, onOpenSettings, onOpenKillSwitch, killSwitchBusy }) {
  return html`
    <div class="topbar">
      <div class="brand">
        <div class="brand-mark">N</div>
        <span>NDHD Trading</span>
      </div>

      ${account &&
      html`<span class="badge ${account.paper ? "paper" : "live"}">
        ${account.paper ? "PAPER" : "LIVE TRADING"}
      </span>`}

      <span class="badge">
        <span class="dot ${connected ? "up" : "down"}" />
        ${connected ? "Live" : "Reconnecting…"}
      </span>

      <div class="topbar-spacer" />

      <button class="kill-switch-btn" disabled=${killSwitchBusy} onClick=${onOpenKillSwitch}>
        ${killSwitchBusy ? "Working…" : "Kill switch"}
      </button>

      <button class="icon-btn" title="Settings" onClick=${onOpenSettings}>⚙</button>
    </div>
  `;
}
