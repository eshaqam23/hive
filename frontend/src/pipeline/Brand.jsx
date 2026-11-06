import React from "react";

// Honeycomb hexagon with a little robot head inside: "hive" + "robots".
function HiveLogo() {
  return (
    <svg className="hive-logo" viewBox="0 0 64 64" role="img" aria-label="Hive logo">
      <defs>
        <linearGradient id="hive-gold" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#ffd966" />
          <stop offset="1" stopColor="#f5b301" />
        </linearGradient>
      </defs>

      {/* slow orbiting dashed ring */}
      <circle className="logo-ring" cx="32" cy="32" r="31" fill="none" stroke="#f5b301" strokeWidth="1.2" strokeDasharray="2 6" />

      {/* outer hexagon */}
      <polygon
        points="32,4 56.2,18 56.2,46 32,60 7.8,46 7.8,18"
        fill="url(#hive-gold)"
        stroke="#2b2418"
        strokeWidth="3"
        strokeLinejoin="round"
      />
      {/* inner honeycomb cell */}
      <polygon
        points="32,10 51,21 51,43 32,54 13,43 13,21"
        fill="none"
        stroke="#2b2418"
        strokeOpacity="0.22"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />

      {/* antenna + signal */}
      <line x1="32" y1="26" x2="32" y2="17" stroke="#2b2418" strokeWidth="2.4" strokeLinecap="round" />
      <circle className="logo-signal" cx="32" cy="15" r="2.6" fill="none" stroke="#2b2418" strokeWidth="1.4" />
      <circle cx="32" cy="15" r="2.6" fill="#fff7e6" stroke="#2b2418" strokeWidth="1.6" />

      {/* head */}
      <rect x="15.5" y="31" width="3.5" height="6" rx="1.5" fill="#2b2418" />
      <rect x="45" y="31" width="3.5" height="6" rx="1.5" fill="#2b2418" />
      <rect x="19" y="26" width="26" height="18" rx="6" fill="#2b2418" />
      <circle className="logo-eye" cx="26.5" cy="34" r="3" fill="#ffd84d" />
      <circle className="logo-eye" cx="37.5" cy="34" r="3" fill="#ffd84d" />
      <rect x="28" y="39.6" width="8" height="1.8" rx="0.9" fill="#f5b301" />
    </svg>
  );
}

// Splits text into spans so each letter can be animated with a staggered delay (--i).
function Letters({ text, start = 0, className = "" }) {
  return (
    <span className={className} aria-hidden="true">
      {[...text].map((char, index) => (
        <span className="ch" key={index} style={{ "--i": start + index }}>
          {char === " " ? " " : char}
        </span>
      ))}
    </span>
  );
}

export default function Brand() {
  return (
    <div className="brand-block">
      <HiveLogo />
      <div className="brand-text">
        <h1 className="hive-title" aria-label="Hive">
          <Letters text="Hive" />
        </h1>
        <p className="credit" aria-label="Developed by Rokesh and Tommy">
          <span className="credit-pre" aria-hidden="true">
            Developed by
          </span>{" "}
          <Letters text="Rokesh" className="name name-a" start={0} />{" "}
          <span className="amp" aria-hidden="true">
            &amp;
          </span>{" "}
          <Letters text="Tommy" className="name name-b" start={8} />
        </p>
      </div>
    </div>
  );
}
