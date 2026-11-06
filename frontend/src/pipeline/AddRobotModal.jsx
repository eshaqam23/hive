import React, { useEffect, useState } from "react";
import { ROBOT_TYPES } from "./sim";

// Tiny side-view illustration for each preset, tinted with the preset colour.
function RobotIcon({ type, color }) {
  return (
    <svg className="type-icon" viewBox="0 0 64 40" aria-hidden="true">
      <rect x="6" y="18" width="52" height="12" rx="4" fill={color} />
      <rect x="10" y="14" width="30" height="6" rx="2" fill={color} opacity="0.55" />
      <circle cx="18" cy="31" r="6" fill="#2b2418" />
      <circle cx="46" cy="31" r="6" fill="#2b2418" />
      <circle cx="18" cy="31" r="2" fill="#f5b301" />
      <circle cx="46" cy="31" r="2" fill="#f5b301" />
      {type === "camera" && (
        <>
          <rect x="24" y="7" width="3" height="8" fill="#44392a" />
          <rect x="19" y="2" width="14" height="8" rx="2" fill="#2b2418" />
          <circle cx="19.5" cy="6" r="2.2" fill="#8fe3f0" />
        </>
      )}
      {type === "lidar" && (
        <>
          <rect x="25" y="9" width="8" height="7" rx="1.5" fill="#44392a" />
          <ellipse cx="29" cy="8" rx="9" ry="3.4" fill="#cfc6f2" stroke="#2b2418" strokeWidth="1.2" />
          <circle cx="36" cy="8" r="1.4" fill="#ff5252" />
        </>
      )}
      {type === "gripper" && (
        <>
          <circle cx="50" cy="18" r="3" fill="#44392a" />
          <path d="M50 18 L60 8" stroke="#e8b04a" strokeWidth="4" strokeLinecap="round" />
          <path d="M58 5 L63 9 M62 12 L63 9" stroke="#2b2418" strokeWidth="2.2" strokeLinecap="round" />
        </>
      )}
    </svg>
  );
}

const Icon = {
  tag: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 12V4h8l10 10-8 8L3 12zm5-5.5a1.5 1.5 0 100 3 1.5 1.5 0 000-3z" />
    </svg>
  ),
  network: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3a9 9 0 100 18 9 9 0 000-18zm0 2c.9 1.2 1.6 2.9 1.9 5h-3.8c.3-2.1 1-3.8 1.9-5zM5.3 10a7 7 0 013.2-4.4C8.2 6.6 8 8 7.9 10H5.3zm0 2h2.6c.1 2 .3 3.4.6 4.4A7 7 0 015.3 12zm4.8 0h3.8c-.3 2.1-1 3.8-1.9 5-.9-1.2-1.6-2.9-1.9-5zm5.9 0h2.7a7 7 0 01-3.3 4.4c.3-1 .5-2.4.6-4.4zm0-2c-.1-2-.3-3.4-.6-4.4A7 7 0 0118.7 10H16z" />
    </svg>
  ),
  hash: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 3L8 8H4v2h3.6l-.8 4H3v2h3.4L5.5 21h2l1-5h4l-1 5h2l1-5H20v-2h-3.6l.8-4H21V8h-3.4l1-5h-2l-1 5h-4l1-5H9zm.1 7h4l-.8 4h-4l.8-4z" />
    </svg>
  ),
};

export default function AddRobotModal({ suggestedDomainId, existingRobots = [], onAdd, onClose }) {
  const [form, setForm] = useState({
    name: "",
    robotType: "camera",
    host: "",
    domainId: suggestedDomainId,
    description: "",
  });

  useEffect(() => {
    const onKey = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function updateField(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
  }

  function stepDomain(delta) {
    setForm((current) => {
      const next = Math.min(232, Math.max(0, (Number(current.domainId) || 0) + delta));
      return { ...current, domainId: next };
    });
  }

  const type = ROBOT_TYPES[form.robotType];
  const clash = existingRobots.find((robot) => Number(robot.domainId) === Number(form.domainId));

  function submit(event) {
    event.preventDefault();
    onAdd({
      name: form.name.trim(),
      robotType: form.robotType,
      host: form.host.trim(),
      domainId: Number(form.domainId),
      description: form.description.trim() || type.summary,
    });
    onClose();
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-robot-title"
        style={{ "--accent": type.color }}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="modal-head">
          <svg className="modal-hex" viewBox="0 0 64 64" aria-hidden="true">
            <polygon
              points="32,4 56.2,18 56.2,46 32,60 7.8,46 7.8,18"
              fill="#f5b301"
              stroke="#2b2418"
              strokeWidth="3"
              strokeLinejoin="round"
            />
            <path d="M32 21v22M21 32h22" stroke="#2b2418" strokeWidth="5" strokeLinecap="round" />
          </svg>
          <div className="modal-head-text">
            <h2 id="add-robot-title">Add a robot</h2>
            <p>Create a profile, then connect it from its node on the board.</p>
          </div>
          <button className="modal-close" type="button" onClick={onClose} aria-label="Close add robot dialog">
            ×
          </button>
        </header>

        <form onSubmit={submit}>
          <div className="modal-body">
            <fieldset className="modal-section">
              <legend>Robot type</legend>
              <div className="type-grid" role="radiogroup" aria-label="Robot type">
                {Object.entries(ROBOT_TYPES).map(([key, value]) => (
                  <button
                    key={key}
                    type="button"
                    role="radio"
                    aria-checked={form.robotType === key}
                    className={`type-card ${form.robotType === key ? "active" : ""}`}
                    style={{ "--accent": value.color }}
                    onClick={() => setForm((current) => ({ ...current, robotType: key }))}
                  >
                    <RobotIcon type={key} color={value.color} />
                    <strong>{value.label.replace(" RC Car", "")}</strong>
                    <span>RC Car</span>
                    <i className="type-check" aria-hidden="true">
                      ✓
                    </i>
                  </button>
                ))}
              </div>
              <p className="type-summary">{type.summary}</p>
            </fieldset>

            <fieldset className="modal-section">
              <legend>Connection</legend>
              <div className="modal-grid">
                <label className="wide">
                  <span className="field-label">Name</span>
                  <span className="field-input">
                    {Icon.tag}
                    <input
                      name="name"
                      value={form.name}
                      onChange={updateField}
                      placeholder="Scout 3"
                      required
                      autoFocus
                    />
                  </span>
                </label>

                <label>
                  <span className="field-label">Robot IP address</span>
                  <span className="field-input">
                    {Icon.network}
                    <input
                      name="host"
                      value={form.host}
                      onChange={updateField}
                      placeholder="192.168.1.20"
                      pattern="[A-Za-z0-9_.:\-]+"
                      title="IP address or hostname"
                      required
                    />
                  </span>
                </label>

                <div className="field-group">
                  <span className="field-label">ROS domain ID</span>
                  <span className="field-input stepper">
                    {Icon.hash}
                    <button type="button" onClick={() => stepDomain(-1)} aria-label="Decrease domain ID">
                      −
                    </button>
                    <input
                      name="domainId"
                      type="number"
                      min="0"
                      max="232"
                      value={form.domainId}
                      onChange={updateField}
                      aria-label="ROS domain ID"
                      required
                    />
                    <button type="button" onClick={() => stepDomain(1)} aria-label="Increase domain ID">
                      +
                    </button>
                  </span>
                  {clash ? (
                    <span className="field-note warn">
                      Already used by {clash.name}. Robots on the same domain share topics.
                    </span>
                  ) : (
                    <span className="field-note">
                      Next free is {suggestedDomainId}.{" "}
                      {Number(form.domainId) !== suggestedDomainId && (
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => setForm((current) => ({ ...current, domainId: suggestedDomainId }))}
                        >
                          Use it
                        </button>
                      )}
                    </span>
                  )}
                </div>
              </div>
            </fieldset>

            <fieldset className="modal-section">
              <legend>
                Notes <em>optional</em>
              </legend>
              <textarea
                name="description"
                value={form.description}
                onChange={updateField}
                placeholder="Short notes about this robot and its role."
                rows={2}
              />
            </fieldset>
          </div>

          <footer className="modal-foot">
            <span className="modal-hint">
              <kbd>Esc</kbd> to close
            </span>
            <div className="modal-actions">
              <button type="button" className="btn-ghost" onClick={onClose}>
                Cancel
              </button>
              <button type="submit" className="btn-primary">
                Add to fleet →
              </button>
            </div>
          </footer>
        </form>
      </section>
    </div>
  );
}
