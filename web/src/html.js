// web/src/html.js - single shared htm tag bound to React.createElement,
// so every component file can write JSX-like markup without a build step.
import React from "react";
import htm from "htm";

export const html = htm.bind(React.createElement);
