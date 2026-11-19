// Icons and marks shared by the shell, the Fleet tab and the robot page (moved from App.jsx).
import React from "react";

/* Drawn here: one stroke weight, currentColor, so every state recolours them.
 * No emoji and no characters standing in for icons. */

export function Icon({ children, size = 20, filled = false, strokeWidth = 2, ...rest }) {
  return (
    <svg
      aria-hidden="true"
      fill={filled ? "currentColor" : "none"}
      focusable="false"
      height={size}
      stroke={filled ? "none" : "currentColor"}
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={strokeWidth}
      viewBox="0 0 24 24"
      width={size}
      {...rest}
    >
      {children}
    </svg>
  );
}

export const HomeIcon = (p) => (
  <Icon {...p}>
    <path d="M3.5 10.5 12 3.5l8.5 7V20a.5.5 0 0 1-.5.5h-5v-6h-6v6H4a.5.5 0 0 1-.5-.5z" />
  </Icon>
);
export const CommandIcon = (p) => (
  <Icon {...p}>
    <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v10a1.5 1.5 0 0 1-1.5 1.5H9l-5 3.5z" />
    <path d="m8 9 2.5 2L8 13M13 13h3" />
  </Icon>
);
export const PlusIcon = (p) => (
  <Icon {...p}>
    <path d="M12 5v14M5 12h14" />
  </Icon>
);
export const PlayIcon = (p) => (
  <Icon filled {...p}>
    <path d="M8 5.3v13.4a1 1 0 0 0 1.5.86l11-6.7a1 1 0 0 0 0-1.72l-11-6.7A1 1 0 0 0 8 5.3z" />
  </Icon>
);
export const PauseIcon = (p) => (
  <Icon filled {...p}>
    <rect height="15" rx="1.2" width="4.2" x="6.2" y="4.5" />
    <rect height="15" rx="1.2" width="4.2" x="13.6" y="4.5" />
  </Icon>
);
export const StopIcon = (p) => (
  <Icon filled {...p}>
    <rect height="12" rx="2" width="12" x="6" y="6" />
  </Icon>
);
export const ArrowUpIcon = (p) => (
  <Icon {...p}>
    <path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" />
  </Icon>
);
export const ArrowDownIcon = (p) => (
  <Icon {...p}>
    <path d="M12 5v14M18.5 12.5 12 19l-6.5-6.5" />
  </Icon>
);
export const RotateLeftIcon = (p) => (
  <Icon {...p}>
    <path d="M4.5 12a7.5 7.5 0 1 0 2.4-5.5" />
    <path d="M4 3.5v4h4" />
  </Icon>
);
export const RotateRightIcon = (p) => (
  <Icon {...p}>
    <path d="M19.5 12a7.5 7.5 0 1 1-2.4-5.5" />
    <path d="M20 3.5v4h-4" />
  </Icon>
);
export const GripperOpenIcon = (p) => (
  <Icon {...p}>
    <path d="M6 4v5.5a6 6 0 0 0 12 0V4" />
    <path d="M12 15.5V20" />
  </Icon>
);
export const GripperCloseIcon = (p) => (
  <Icon {...p}>
    <path d="M9 4v5.5a3 3 0 0 0 6 0V4" />
    <path d="M12 12.5V20" />
  </Icon>
);
export const SendIcon = (p) => (
  <Icon {...p}>
    <path d="M12 19V5M6 11l6-6 6 6" />
  </Icon>
);
export const CloseIcon = (p) => (
  <Icon {...p}>
    <path d="m6 6 12 12M18 6 6 18" />
  </Icon>
);
export const TrashIcon = (p) => (
  <Icon {...p}>
    <path d="M4.5 6.5h15M9.5 6.5V4.5h5v2M6.5 6.5l.8 12.6a1.5 1.5 0 0 0 1.5 1.4h6.4a1.5 1.5 0 0 0 1.5-1.4l.8-12.6M10 10.5v6M14 10.5v6" />
  </Icon>
);
export const PencilIcon = (p) => (
  <Icon {...p}>
    <path d="M4.5 19.5h4l10.3-10.3a2 2 0 0 0 0-2.8l-1.2-1.2a2 2 0 0 0-2.8 0L4.5 15.5v4ZM13.5 6.5l4 4" />
  </Icon>
);
export const ChevronLeftIcon = (p) => (
  <Icon {...p}>
    <path d="M14.5 6 8.5 12l6 6" />
  </Icon>
);
export const SunIcon = (p) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" />
  </Icon>
);
export const MoonIcon = (p) => (
  <Icon {...p}>
    <path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10Z" />
  </Icon>
);
export const GearIcon = (p) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" />
  </Icon>
);
export const Spinner = (p) => (
  <Icon className="animate-spin-slow" {...p}>
    <path d="M12 3.5a8.5 8.5 0 1 1-8.5 8.5" />
  </Icon>
);

/* The Hive mark: a hexagon cell with a cell inside it. */
export function HiveMark({ size = 28, className = "" }) {
  return (
    <svg aria-hidden="true" className={className} height={size} viewBox="0 0 32 32" width={size}>
      <path
        d="M16 1.8 28.3 8.9v14.2L16 30.2 3.7 23.1V8.9zM16 7.4 8.6 11.7v8.6l7.4 4.3 7.4-4.3v-8.6zM16 12.2l3.3 1.9v3.8L16 19.8l-3.3-1.9v-3.8z"
        fill="currentColor"
        fillRule="evenodd"
      />
    </svg>
  );
}

/* Spotify's "now playing" bars, here meaning "connected". Static under reduced motion. */
export function SignalBars({ className = "" }) {
  return (
    <span aria-hidden="true" className={`flex h-3.5 items-end gap-[2px] ${className}`}>
      {[0, 0.35, 0.7].map((delay) => (
        <span
          className="h-full w-[3px] origin-bottom animate-signal rounded-full bg-live"
          key={delay}
          style={{ animationDelay: `-${delay}s` }}
        />
      ))}
    </span>
  );
}
