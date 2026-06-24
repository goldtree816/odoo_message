/** @odoo-module **/

/**
 * WhatsApp Dashboard — Odoo 19
 *
 * Uses OWL 2 (built into Odoo 19) and the standard useService('rpc')
 * hook for all server calls.  No fetch(), no XMLHttpRequest, no mock.
 *
 * Twilio sending is handled server-side in controllers/main.py.
 * This file is pure UI — it calls the Odoo JSON routes and reacts
 * to the responses.
 */

import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { registry }   from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";


// ─── Seed data — displayed immediately while the first server call is in flight ─

const SEED_THREADS = [
    { id:1,  name:"Azure Interiors",   initials:"AC", color:"#25D366", last_message:"Thank you! Please share the quotation.", time:"10:30 AM", unread:2, status:"online",  type:"external" },
    { id:2,  name:"Global Solutions",  initials:"GL", color:"#128C7E", last_message:"We are looking for 20 units.",            time:"9:45 AM",  unread:1, status:"offline", type:"external" },
    { id:3,  name:"Green Planet Co.",  initials:"GP", color:"#34B7F1", last_message:"When will the order be delivered?",       time:"Yesterday",unread:0, status:"offline", type:"external" },
    { id:4,  name:"Star Technologies", initials:"ST", color:"#FF6B35", last_message:"Please check the attachment.",            time:"Yesterday",unread:3, status:"offline", type:"external" },
    { id:5,  name:"Modern Home",       initials:"MH", color:"#6C5CE7", last_message:"Thanks for the update!",                 time:"Monday",   unread:0, status:"offline", type:"internal" },
    { id:6,  name:"Bright Retail",     initials:"BR", color:"#E17055", last_message:"Can we schedule a demo?",                time:"Monday",   unread:0, status:"offline", type:"external" },
    { id:7,  name:"Next Wave LLC",     initials:"NW", color:"#00B894", last_message:"Looking forward to working together.",   time:"Sunday",   unread:0, status:"offline", type:"internal" },
];

const SEED_MESSAGES = {
    1: [
        { id:1, body:"Hello, I'm interested in your office furniture collection.",                                        time:"10:28 AM", direction:"incoming", type:"external", status:"read"      },
        { id:2, body:"Hello! Thanks for reaching out. How can I help you today?",                                        time:"10:29 AM", direction:"outgoing", type:"external", status:"read"      },
        { id:3, body:"I need a quotation for 10 office chairs and 5 meeting tables.",                                    time:"10:29 AM", direction:"incoming", type:"external", status:"delivered" },
        { id:4, body:"Sure! Could you share your email so I can send the quotation?",                                    time:"10:30 AM", direction:"outgoing", type:"external", status:"read"      },
        { id:5, body:"Thank you! Please share the quotation.",                                                           time:"10:30 AM", direction:"incoming", type:"external", status:"delivered" },
    ],
    2: [
        { id:1, body:"Hi, we are looking for 20 units of your premium chairs.",  time:"9:40 AM", direction:"incoming", type:"external", status:"read"      },
        { id:2, body:"Of course! Let me prepare a bulk quotation for you.",       time:"9:43 AM", direction:"outgoing", type:"external", status:"read"      },
        { id:3, body:"We are looking for 20 units.",                              time:"9:45 AM", direction:"incoming", type:"external", status:"delivered" },
    ],
};

// Pool of auto-replies simulated locally (not from Twilio)
const AUTO_REPLIES = [
    "Got it, thank you!",
    "We'll get back to you shortly.",
    "Sure, let me check and confirm.",
    "Thanks for the information!",
    "Perfect, we'll proceed with that.",
    "Understood, I'll follow up soon.",
    "Noted! We appreciate your prompt response.",
];


// ─── Tiny helper functions ────────────────────────────────────────────────────

/** Returns the current time as "H:MM AM/PM" */
function currentTime() {
    const d = new Date();
    let h = d.getHours(), m = d.getMinutes();
    const ampm = h >= 12 ? "PM" : "AM";
    h = h % 12 || 12;
    return `${h}:${String(m).padStart(2, "0")} ${ampm}`;
}

/** Formats a number of seconds as "MM:SS" */
function formatSeconds(totalSec) {
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** Returns a random auto-reply string */
function randomReply() {
    return AUTO_REPLIES[Math.floor(Math.random() * AUTO_REPLIES.length)];
}


// ─── Main OWL Component ───────────────────────────────────────────────────────

export class WhatsAppDashboard extends Component {
    static template = "whatsapp_dashboard.WhatsAppDashboard";
    static props    = {
        '*': true,   // FIX: allow any props (Odoo passes action, actionId, etc.)
    };

    setup() {
        // ── Odoo services ──────────────────────────────────────────────────
        this.rpc = rpc;
        this.notification = useService("notification");

        // DOM ref so we can auto-scroll the message feed
        this.messagesRef = useRef("messages");

        // Private: file attachment ID pending send
        this._pendingMediaId = null;

        // ── Reactive state ─────────────────────────────────────────────────
        this.state = useState({
            threads:        [],       // all conversation threads
            messages:       [],       // messages for the open thread
            activeThread:   null,     // currently selected thread object
            searchQuery:    "",
            activeFilter:   "All",    // "All" | "External" | "Internal Notes"
            draftMessage:   "",
            msgType:        "external", // "external" | "internal"
            isTyping:       false,
            showAttachMenu: false,
            showInfoPanel:  false,    // contact info slide-in

            // Call UI
            showVoiceCall:  false,
            showVideoCall:  false,
            callStatus:     "Calling...",
            callConnected:  false,
            callTimer:      "00:00",
            isMuted:        false,
            speakerOn:      false,
            cameraOff:      false,

            // ── SIDEBAR STATE ──
            sidebarCollapsed: false,
            activeNavItem: "crm",
        });

        // Private: call timer + poll intervals
        this._callInterval = null;
        this._pollInterval = null;
        this._callSeconds  = 0;
        this._lastMsgId    = 0;

        onMounted(()     => this._init());
        onWillUnmount(() => this._cleanup());
    }


    // ── Initialisation ───────────────────────────────────────────────────────

    async _init() {
        this.state.threads = [...SEED_THREADS];
        await this._loadThreads();
        this._pollInterval = setInterval(() => this._poll(), 8000);
    }

    _cleanup() {
        this._clearCallTimer();
        if (this._pollInterval) {
            clearInterval(this._pollInterval);
            this._pollInterval = null;
        }
    }


    // ── Thread list ──────────────────────────────────────────────────────────

    async _loadThreads() {
        try {
            const res = await this.rpc("/whatsapp_dashboard/threads", {});
            if (res && res.threads && res.threads.length) {
                this.state.threads = res.threads;
            }
        } catch (_) {
            // seed data already shown — silent fallback
        }
    }

    get filteredThreads() {
        const q = this.state.searchQuery.toLowerCase().trim();
        return this.state.threads.filter((t) => {
            const matchSearch =
                !q ||
                t.name.toLowerCase().includes(q) ||
                (t.last_message || "").toLowerCase().includes(q);

            const matchFilter =
                this.state.activeFilter === "All" ||
                (this.state.activeFilter === "External"       && t.type === "external") ||
                (this.state.activeFilter === "Internal Notes" && t.type === "internal");

            return matchSearch && matchFilter;
        });
    }


    // ── Thread selection ─────────────────────────────────────────────────────

    async selectThread(thread) {
        this.state.showInfoPanel  = false;
        this.state.showAttachMenu = false;
        this.state.activeThread   = thread;
        this.state.draftMessage   = "";
        this.state.msgType        = "external";
        this.state.messages       = SEED_MESSAGES[thread.id] || [];
        this._lastMsgId           = 0;
        this._pendingMediaId      = null;

        const t = this.state.threads.find((x) => x.id === thread.id);
        if (t) t.unread = 0;

        await this._loadMessages(thread.id);
        this.rpc("/whatsapp_dashboard/mark_read", { thread_id: thread.id }).catch(() => {});
    }

    async _loadMessages(threadId) {
        try {
            const res = await this.rpc("/whatsapp_dashboard/messages", { thread_id: threadId });
            if (res && res.messages && res.messages.length) {
                this.state.messages = res.messages;
                this._lastMsgId = res.messages[res.messages.length - 1].id;
            }
        } catch (_) {}
        this._scrollToBottom();
    }


    // ── Real-time polling for new incoming messages ───────────────────────────

    async _poll() {
        if (!this.state.activeThread) return;
        try {
            const res = await this.rpc("/whatsapp_dashboard/poll", {
                thread_id:       this.state.activeThread.id,
                last_message_id: this._lastMsgId,
            });
            if (res.new_messages && res.new_messages.length) {
                this.state.messages = [...this.state.messages, ...res.new_messages];
                this._lastMsgId = res.new_messages[res.new_messages.length - 1].id;
                this._scrollToBottom();
            }
            if (res.threads && res.threads.length) {
                this.state.threads = res.threads;
            }
        } catch (_) {}
    }


    // ── Filters ──────────────────────────────────────────────────────────────

    setFilter(tab) { this.state.activeFilter = tab; }


    // ── Message type toggle ─────────────────────────────────────────────────

    toggleMsgType() {
        this.state.msgType = this.state.msgType === "external" ? "internal" : "external";
    }


    // ── Send message ─────────────────────────────────────────────────────────

    async sendMessage() {
        const body = this.state.draftMessage.trim();
        if (!body && !this._pendingMediaId) return;

        const finalBody = body || '📎 Attachment';

        // Optimistic UI
        const optimistic = {
            id:        Date.now(),
            body:      finalBody,
            time:      currentTime(),
            direction: "outgoing",
            type:      this.state.msgType,
            status:    "sent",
        };
        this.state.messages     = [...this.state.messages, optimistic];
        this.state.draftMessage = "";
        this._scrollToBottom();

        const t = this.state.threads.find((x) => x.id === this.state.activeThread.id);
        if (t) { t.last_message = finalBody; t.time = currentTime(); }

        if (this.state.msgType === "external" && !this._pendingMediaId) {
            this._simulateReply();
        }

        try {
            const res = await this.rpc("/whatsapp_dashboard/send_message", {
                thread_id: this.state.activeThread.id,
                body:      finalBody,
                msg_type:  this.state.msgType,
                media_id:  this._pendingMediaId || null,
            });
            if (res && res.twilio_sid) {
                this.notification.add("Message sent via WhatsApp", { type: "success", sticky: false });
            }
            this._pendingMediaId = null;
        } catch (_) {}
    }

    onKeyDown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.sendMessage();
        }
    }

    /** Local simulated reply — typing dots then a random reply string */
    _simulateReply() {
        this.state.isTyping = true;
        setTimeout(() => {
            this.state.isTyping = false;
            const body = randomReply();
            this.state.messages = [
                ...this.state.messages,
                { id: Date.now(), body, time: currentTime(), direction: "incoming", type: "external", status: null },
            ];
            this._scrollToBottom();
            const t = this.state.threads.find((x) => x.id === this.state.activeThread.id);
            if (t) { t.last_message = body; t.time = currentTime(); t.unread = 0; }
        }, 1800);
    }

    _scrollToBottom() {
        setTimeout(() => {
            const el = this.messagesRef.el;
            if (el) el.scrollTop = el.scrollHeight;
        }, 40);
    }


    // ── Attachment menu ───────────────────────────────────────────────────────

    toggleAttachMenu() { this.state.showAttachMenu = !this.state.showAttachMenu; }

    async handleAttachment(type) {
        this.state.showAttachMenu = false;
        const input = document.createElement('input');
        input.type = 'file';
        if (type === 'photos') {
            input.accept = 'image/*,video/*';
        } else {
            input.accept = '.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.zip,.rar';
        }
        input.onchange = async (ev) => {
            const file = ev.target.files[0];
            if (!file) return;
            const formData = new FormData();
            formData.append('file', file);
            try {
                const response = await fetch('/whatsapp_dashboard/upload_media', {
                    method: 'POST',
                    body: formData,
                });
                const result = await response.json();
                if (result.attachment_id) {
                    this._pendingMediaId = result.attachment_id;
                    this.state.draftMessage = this.state.draftMessage || '📎 ';
                    this.notification.add(`File "${file.name}" ready to send.`, { type: 'success' });
                } else {
                    this.notification.add('Upload failed: ' + (result.error || 'Unknown'), { type: 'danger' });
                }
            } catch (e) {
                console.error('Upload error:', e);
                this.notification.add('Upload error. Check console.', { type: 'danger' });
            }
        };
        input.click();
    }


    // ── Contact info panel ────────────────────────────────────────────────────

    openInfoPanel()  { this.state.showInfoPanel = true;  }
    closeInfoPanel() { this.state.showInfoPanel = false; }


    // ── Voice call ────────────────────────────────────────────────────────────

    startVoiceCall() {
        this.state.showVoiceCall = true;
        this.state.callStatus    = "Calling...";
        this.state.callConnected = false;
        this.state.isMuted       = false;
        this.state.speakerOn     = false;
        this._callSeconds        = 0;
        this.state.callTimer     = "00:00";
        this._startRinging();
    }


    // ── Video call ────────────────────────────────────────────────────────────

    startVideoCall() {
        this.state.showVideoCall = true;
        this.state.callStatus    = "Connecting...";
        this.state.callConnected = false;
        this.state.isMuted       = false;
        this.state.cameraOff     = false;
        this._callSeconds        = 0;
        this.state.callTimer     = "00:00";
        this._startRinging();
    }

    _startRinging() {
        setTimeout(() => {
            if (!this.state.showVoiceCall && !this.state.showVideoCall) return;
            this.state.callStatus    = "Connected";
            this.state.callConnected = true;
            this._clearCallTimer();
            this._callInterval = setInterval(() => {
                this._callSeconds++;
                this.state.callTimer = formatSeconds(this._callSeconds);
            }, 1000);
        }, 2500);
    }

    endCall() {
        this.state.showVoiceCall = false;
        this.state.showVideoCall = false;
        this.state.callConnected = false;
        this.state.callStatus    = "Calling...";
        this.state.callTimer     = "00:00";
        this._clearCallTimer();
    }

    _clearCallTimer() {
        if (this._callInterval) {
            clearInterval(this._callInterval);
            this._callInterval = null;
        }
    }

    toggleMute()    { this.state.isMuted   = !this.state.isMuted;   }
    toggleSpeaker() { this.state.speakerOn = !this.state.speakerOn; }
    toggleCamera()  { this.state.cameraOff = !this.state.cameraOff; }


    // ─── SIDEBAR METHODS ─────────────────────────────────────────────────────

    toggleSidebar() {
        this.state.sidebarCollapsed = !this.state.sidebarCollapsed;
    }

    setNavItem(id) {
        this.state.activeNavItem = id;
    }
}

// Register under the tag referenced by ir.actions.client
registry.category("actions").add("whatsapp_dashboard", WhatsAppDashboard);