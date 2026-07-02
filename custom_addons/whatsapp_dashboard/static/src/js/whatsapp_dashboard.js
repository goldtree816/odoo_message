/** @odoo-module **/

import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";

const SEED_THREADS = [
    {id: 1, name: "Azure Interiors", initials: "AI", color: "#25D366", last_message: "Thank you! Please share the quotation.", time: "10:30 AM", unread: 2, status: "online", type: "external"},
    {id: 2, name: "Global Solutions", initials: "GS", color: "#128C7E", last_message: "We are looking for 20 units.", time: "9:45 AM", unread: 1, status: "offline", type: "external"},
    {id: 3, name: "Green Planet Co.", initials: "GP", color: "#34B7F1", last_message: "When will the order be delivered?", time: "Yesterday", unread: 0, status: "offline", type: "external"},
    {id: 4, name: "Star Technologies", initials: "ST", color: "#FF6B35", last_message: "Please check the attachment.", time: "Yesterday", unread: 3, status: "offline", type: "external"},
    {id: 5, name: "Modern Home", initials: "MH", color: "#6C5CE7", last_message: "Thanks for the update!", time: "Monday", unread: 0, status: "offline", type: "internal"},
    {id: 6, name: "Bright Retail", initials: "BR", color: "#E17055", last_message: "Can we schedule a demo?", time: "Monday", unread: 0, status: "offline", type: "external"},
];

const SEED_MESSAGES = {
    1: [
        {id: 1, body: "Hello, I'm interested in your office furniture collection.", time: "10:28 AM", direction: "incoming", type: "external", status: "read"},
        {id: 2, body: "Hello! Thanks for reaching out. How can I help you today?", time: "10:29 AM", direction: "outgoing", type: "external", status: "read"},
        {id: 3, body: "I need a quotation for 10 office chairs and 5 meeting tables.", time: "10:29 AM", direction: "incoming", type: "external", status: "delivered"},
        {id: 4, body: "Sure! Could you share your email so I can send the quotation?", time: "10:30 AM", direction: "outgoing", type: "external", status: "read"},
        {id: 5, body: "Thank you! Please share the quotation.", time: "10:30 AM", direction: "incoming", type: "external", status: "delivered"},
    ],
    2: [
        {id: 1, body: "Hi, we are looking for 20 units of your premium chairs.", time: "9:40 AM", direction: "incoming", type: "external", status: "read"},
        {id: 2, body: "Of course! Let me prepare a bulk quotation for you.", time: "9:43 AM", direction: "outgoing", type: "external", status: "read"},
        {id: 3, body: "We are looking for 20 units.", time: "9:45 AM", direction: "incoming", type: "external", status: "delivered"},
    ],
};

const AUTO_REPLIES = [
    "Got it, thank you!",
    "We'll get back to you shortly.",
    "Sure, let me check and get back to you.",
    "Thanks for the information!",
    "Perfect, we'll proceed with that.",
    "Understood, I'll follow up soon.",
    "Noted! We appreciate your prompt response.",
];

const AVAILABLE_NUMBERS_FALLBACK = [
    {id: 1, number: '+977 980-1234567', type: 'Mobile', capabilities: 'SMS, Voice, WhatsApp', monthlyCost: '5.00', setupFee: '0.00'},
    {id: 2, number: '+977 980-2345678', type: 'Mobile', capabilities: 'SMS, Voice, WhatsApp', monthlyCost: '5.00', setupFee: '0.00'},
    {id: 3, number: '+977 980-3456789', type: 'Mobile', capabilities: 'SMS, Voice', monthlyCost: '3.50', setupFee: '0.00'},
    {id: 4, number: '+977 980-4567890', type: 'Mobile', capabilities: 'SMS, Voice, WhatsApp', monthlyCost: '5.00', setupFee: '0.00'},
    {id: 5, number: '+977 980-5678901', type: 'Mobile', capabilities: 'SMS, Voice', monthlyCost: '3.50', setupFee: '0.00'},
    {id: 6, number: '+977 980-6789012', type: 'Mobile', capabilities: 'SMS, Voice, WhatsApp', monthlyCost: '5.00', setupFee: '0.00'},
];

function currentTime() {
    const d = new Date();
    let h = d.getHours(), m = d.getMinutes();
    const ampm = h >= 12 ? "PM" : "AM";
    h = h % 12 || 12;
    return `${h}:${String(m).padStart(2, '0')} ${ampm}`;
}

function randomReply() {
    return AUTO_REPLIES[Math.floor(Math.random() * AUTO_REPLIES.length)];
}

function formatFileSize(bytes) {
    if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return bytes + ' B';
}

function getFileTypeCategory(mimetype, filename) {
    const ext = (filename || "").split('.').pop().toLowerCase();
    if (mimetype && mimetype.startsWith('image/')) return 'image';
    if (mimetype && mimetype.startsWith('video/')) return 'video';
    if (mimetype && mimetype.startsWith('audio/')) return 'audio';
    if (['pdf'].includes(ext)) return 'pdf';
    if (['doc', 'docx'].includes(ext)) return 'word';
    if (['xls', 'xlsx'].includes(ext)) return 'excel';
    if (['ppt', 'pptx'].includes(ext)) return 'ppt';
    if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return 'archive';
    return 'document';
}

export class WhatsAppDashboard extends Component {
    static template = "whatsapp_dashboard.WhatsAppDashboard";
    static props = {};

    setup() {
        this.rpc = rpc;
        this.notification = useService("notification");
        this.messagesRef = useRef("messages");
        this._pendingMediaId = null;
        this._stripePublishableKey = null;

        // State
        this.state = useState({
            // Chat
            threads: [],
            messages: [],
            activeThread: null,
            searchQuery: "",
            activeFilter: "All",
            draftMessage: "",
            msgType: "external",
            isTyping: false,
            showAttachMenu: false,
            showInfoPanel: false,
            showAddUserModal: false,
            showFilePreview: false,
            addUserName: "",
            addUserPhone: "",
            sidebarCollapsed: false,
            activeNavItem: "dashboard",
            currentView: "dashboard",

            // Phone Numbers
            phoneTab: "my_numbers",
            buyCountry: "NP",
            availableNumbers: [],
            myNumbers: [
                {number: '+977 980-1234567', status: 'Active'},
                {number: '+977 981-7654321', status: 'Pending'},
            ],
            isLoadingNumbers: false,
            currentPage: 1,
            itemsPerPage: 10,

            // Stripe payment
            showPaymentModal: false,
            paymentAmount: "99.00",
            paymentPlan: "Pro Plan",
            paymentDescription: "WhatsApp Dashboard Subscription",
            isProcessingPayment: false,
            paymentError: null,
            cardHolderName: "",
            cardEmail: "",
            cardCountry: "US",
            cardZip: "",

            // Subaccounts
            subaccounts: [],
            totalPhoneNumbers: 0,
            totalSmsSent: 0,
            totalVoiceMinutes: 0,
            subSearchQuery: "",
            filterStatus: "all",
            filterType: "all",
            showCreateModal: false,
            showEditModal: false,
            showDetailPanel: false,
            selectedSubaccount: null,
            creating: false,

            // File attachment
            pendingFile: null,

            // New Subaccount
            newSubaccount: {
                name: "",
                uniqueName: "",
                email: "",
                status: "active",
                subaccountType: "standard",
                capabilities: { voice: true, sms: true, mms: true, whatsapp: true }
            },
            editSubaccount: {
                id: null,
                name: "",
                uniqueName: "",
                email: "",
                status: "active"
            },
        });

        this._pollInterval = null;
        this._lastMsgId = 0;

        onMounted(() => this._init());
        onWillUnmount(() => this._cleanup());
    }

    // ========== INITIALIZATION ==========

    async _init() {
        this.state.threads = [...SEED_THREADS];
        await this._loadThreads();
        await this._loadStripeConfig();
        this._pollInterval = setInterval(() => this._poll(), 8000);
    }

    _cleanup() {
        if (this._pollInterval) {
            clearInterval(this._pollInterval);
            this._pollInterval = null;
        }
        if (this.state.pendingFile && this.state.pendingFile.objectUrl) {
            URL.revokeObjectURL(this.state.pendingFile.objectUrl);
        }
    }

    async _loadStripeConfig() {
        try {
            const res = await this.rpc("/whatsapp_dashboard/stripe/config", {});
            if (res && res.publishable_key) {
                this._stripePublishableKey = res.publishable_key;
            }
        } catch (e) {
            console.error("Failed to load Stripe config:", e);
        }
    }

    async _loadThreads() {
        try {
            const res = await this.rpc("/whatsapp_dashboard/threads", {});
            if (res && res.threads && res.threads.length) {
                this.state.threads = res.threads;
            }
        } catch (e) {
            // Silently fallback to seed data
        }
    }

    // ========== COMPUTED PROPERTIES ==========

    get filteredThreads() {
        const q = this.state.searchQuery.toLowerCase().trim();
        return this.state.threads.filter(t => {
            const matchSearch = !q || t.name.toLowerCase().includes(q) || (t.last_message || "").toLowerCase().includes(q);
            const matchFilter = this.state.activeFilter === "All" ||
                (this.state.activeFilter === "External" && t.type === "external") ||
                (this.state.activeFilter === "Internal Notes" && t.type === "internal");
            return matchSearch && matchFilter;
        });
    }

    get filteredSubaccounts() {
        let result = this.state.subaccounts;
        const q = this.state.subSearchQuery.toLowerCase().trim();
        if (q) {
            result = result.filter(s =>
                s.name.toLowerCase().includes(q) ||
                s.unique_name.toLowerCase().includes(q) ||
                (s.email || '').toLowerCase().includes(q)
            );
        }
        if (this.state.filterStatus !== "all") {
            result = result.filter(s => s.status === this.state.filterStatus);
        }
        if (this.state.filterType !== "all") {
            result = result.filter(s => s.subaccount_type === this.state.filterType);
        }
        return result;
    }

    get paginatedNumbers() {
        const start = (this.state.currentPage - 1) * this.state.itemsPerPage;
        const end = start + this.state.itemsPerPage;
        return this.state.availableNumbers.slice(start, end);
    }

    get totalPages() {
        return Math.ceil(this.state.availableNumbers.length / this.state.itemsPerPage);
    }

    get canSend() {
        return this.state.draftMessage.trim().length > 0 ||
            (this.state.pendingFile && this.state.pendingFile.attachmentId);
    }

    // ========== THREAD / MESSAGE FUNCTIONS ==========

    async selectThread(thread) {
        this.state.showInfoPanel = false;
        this.state.showAttachMenu = false;
        this.state.showFilePreview = false;
        this.state.activeThread = thread;
        this.state.draftMessage = "";
        this.state.msgType = "external";
        this.state.messages = SEED_MESSAGES[thread.id] || [];
        this._lastMsgId = 0;
        this._pendingMediaId = null;
        this._clearPendingFile();

        const t = this.state.threads.find((x) => x.id === thread.id);
        if (t) t.unread = 0;

        await this._loadMessages(thread.id);
        this.rpc("/whatsapp_dashboard/mark_read", {thread_id: thread.id}).catch(() => {});
    }

    async _loadMessages(threadId) {
        try {
            const res = await this.rpc("/whatsapp_dashboard/messages", {thread_id: threadId});
            if (res && res.messages && res.messages.length) {
                this.state.messages = res.messages;
                this._lastMsgId = res.messages[res.messages.length - 1].id;
            }
        } catch (e) {
            // Use seed data
        }
        this._scrollToBottom();
    }

    async _poll() {
        if (!this.state.activeThread) return;
        try {
            const res = await this.rpc("/whatsapp_dashboard/poll", {
                thread_id: this.state.activeThread.id,
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
        } catch (e) {}
    }

    setFilter(tab) {
        this.state.activeFilter = tab;
    }

    toggleMsgType() {
        this.state.msgType = this.state.msgType === "external" ? "internal" : "external";
    }

    // ========== SEND MESSAGE ==========

    async sendMessage() {
        const body = this.state.draftMessage.trim();
        const hasFile = this.state.pendingFile && this.state.pendingFile.attachmentId;
        if (!body && !hasFile) return;

        const displayBody = hasFile ? (body || this.state.pendingFile.name) : body;

        const optimistic = {
            id: Date.now(),
            body: displayBody,
            time: currentTime(),
            direction: "outgoing",
            type: this.state.msgType,
            status: "sent",
        };

        if (hasFile) {
            optimistic.attachment = {
                id: this.state.pendingFile.attachmentId,
                name: this.state.pendingFile.name,
                size: formatFileSize(this.state.pendingFile.size),
                fileType: this.state.pendingFile.fileType,
                url: "/web/content/" + this.state.pendingFile.attachmentId + "?download=true",
                objectUrl: this.state.pendingFile.objectUrl,
                isImage: this.state.pendingFile.fileType === 'image',
            };
        }

        this.state.messages = [...this.state.messages, optimistic];
        this.state.draftMessage = "";
        this.state.showAttachMenu = false;
        this.state.showFilePreview = false;
        this._scrollToBottom();

        const t = this.state.threads.find((x) => x.id === this.state.activeThread.id);
        if (t) {
            t.last_message = displayBody;
            t.time = currentTime();
        }

        if (this.state.msgType === "external" && !hasFile) {
            this._simulateReply();
        }

        try {
            const res = await this.rpc("/whatsapp_dashboard/send_message", {
                thread_id: this.state.activeThread.id,
                body: body || (hasFile ? this.state.pendingFile.name : ""),
                msg_type: this.state.msgType,
                media_id: hasFile ? this.state.pendingFile.attachmentId : null,
            });
            if (res && res.twilio_sid) {
                this.notification.add("Message sent via WhatsApp", {type: "success", sticky: false});
            }
            if (res && res.error) {
                this.notification.add(res.error, {type: "danger"});
            }
        } catch (e) {
            this.notification.add("Failed to send message", {type: "danger"});
        } finally {
            this._pendingMediaId = null;
            this._clearPendingFile();
        }
    }

    onKeyDown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.sendMessage();
        }
    }

    _simulateReply() {
        this.state.isTyping = true;
        setTimeout(() => {
            this.state.isTyping = false;
            const body = randomReply();
            this.state.messages = [
                ...this.state.messages,
                {id: Date.now(), body, time: currentTime(), direction: "incoming", type: "external", status: null},
            ];
            this._scrollToBottom();
            const t = this.state.threads.find((x) => x.id === this.state.activeThread.id);
            if (t) {
                t.last_message = body;
                t.time = currentTime();
                t.unread = 0;
            }
        }, 1800);
    }

    _scrollToBottom() {
        setTimeout(() => {
            const el = this.messagesRef.el;
            if (el) el.scrollTop = el.scrollHeight;
        }, 40);
    }

    // ========== FILE ATTACHMENT ==========

    toggleAttachMenu() {
        this.state.showAttachMenu = !this.state.showAttachMenu;
    }

    _clearPendingFile() {
        if (this.state.pendingFile && this.state.pendingFile.objectUrl) {
            URL.revokeObjectURL(this.state.pendingFile.objectUrl);
        }
        this.state.pendingFile = null;
        this.state.showFilePreview = false;
        this._pendingMediaId = null;
    }

    removePendingFile() {
        this._clearPendingFile();
    }

    closeFilePreview() {
        this._clearPendingFile();
    }

    handleAttachment(type) {
        this.state.showAttachMenu = false;
        const input = document.createElement('input');
        input.type = 'file';
        if (type === 'photos') {
            input.accept = 'image/*,video/*';
        } else {
            input.accept = '.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.zip,.rar,.csv';
        }
        input.onchange = (ev) => {
            const file = ev.target.files[0];
            if (!file) return;
            this._uploadFile(file);
        };
        input.click();
    }

    _uploadFile(file) {
        const fileType = getFileTypeCategory(file.type, file.name);
        const objectUrl = URL.createObjectURL(file);

        this.state.pendingFile = {
            name: file.name,
            size: file.size,
            sizeFormatted: formatFileSize(file.size),
            type: file.type,
            fileType: fileType,
            attachmentId: null,
            objectUrl: objectUrl,
            uploading: true,
            isImage: fileType === 'image',
            isVideo: fileType === 'video',
        };

        this.state.showFilePreview = true;

        const formData = new FormData();
        formData.append('file', file);

        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/whatsapp_dashboard/upload_media', true);

        xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
                try {
                    const result = JSON.parse(xhr.responseText);
                    if (result.attachment_id) {
                        this.state.pendingFile = {
                            ...this.state.pendingFile,
                            attachmentId: result.attachment_id,
                            uploading: false,
                        };
                        this._pendingMediaId = result.attachment_id;
                    } else {
                        this.notification.add('Upload failed: ' + (result.error || 'Unknown'), {type: 'danger'});
                        this._clearPendingFile();
                    }
                } catch (e) {
                    this.notification.add('Upload failed: invalid response', {type: 'danger'});
                    this._clearPendingFile();
                }
            } else {
                this.notification.add('Upload failed (server error ' + xhr.status + ')', {type: 'danger'});
                this._clearPendingFile();
            }
        };

        xhr.onerror = () => {
            this.notification.add('Upload failed: network error', {type: 'danger'});
            this._clearPendingFile();
        };

        xhr.send(formData);
    }

    // ========== USER / INFO PANEL ==========

    openInfoPanel() {
        this.state.showInfoPanel = true;
    }

    closeInfoPanel() {
        this.state.showInfoPanel = false;
    }

    openAddUserModal() {
        this.state.showAddUserModal = true;
        this.state.addUserName = "";
        this.state.addUserPhone = "";
    }

    closeAddUserModal() {
        this.state.showAddUserModal = false;
    }

    async addExternalUser() {
        const phone = this.state.addUserPhone.trim();
        if (!phone) return;
        const name = this.state.addUserName.trim() || phone;
        try {
            const res = await this.rpc("/whatsapp_dashboard/create_thread", {name, phone});
            if (res && res.error) {
                this.notification.add(res.error, {type: 'danger'});
                return;
            }
            if (res && res.success) {
                this.state.threads = [res.thread_data, ...this.state.threads];
                this.notification.add(`External user "${name}" added`, {type: 'success'});
            }
        } catch (e) {
            this.notification.add('Error adding user', {type: 'danger'});
        }
        this.closeAddUserModal();
    }

    // ========== NAVIGATION ==========

    toggleSidebar() {
        this.state.sidebarCollapsed = !this.state.sidebarCollapsed;
    }

    setNavItem(id) {
        this.state.activeNavItem = id;
        if (id === 'dashboard') {
            this.state.currentView = 'dashboard';
        } else if (id === 'phone_numbers') {
            this.state.currentView = 'phone_numbers';
            this.state.phoneTab = 'my_numbers';
        } else if (id === 'subaccounts') {
            this.state.currentView = 'subaccounts';
            this.loadSubaccounts();
        } else if (id === 'settings') {
            this.state.currentView = 'settings';
            try {
                this.env.services.action.doAction('base.action_general_configuration');
            } catch (e) {
                window.open('/odoo/settings', '_blank');
            }
        } else if (id === 'buy_number') {
            this.state.phoneTab = 'buy_number';
            this.searchNumbers();
        }
    }

    // ========== PHONE NUMBERS ==========

    async searchNumbers() {
        this.state.isLoadingNumbers = true;
        this.state.currentPage = 1;
        this.notification.add('Searching for available numbers...', {type: 'info'});

        try {
            const res = await this.rpc("/whatsapp_dashboard/available_numbers", {
                country_code: this.state.buyCountry,
                number_type: 'local',
            });

            if (res && res.error) {
                this.notification.add('Error: ' + res.error, {type: 'danger'});
                this.state.availableNumbers = [...AVAILABLE_NUMBERS_FALLBACK];
            } else if (res && res.numbers) {
                if (res.numbers.length === 0) {
                    this.notification.add('No numbers available for this country.', {type: 'warning'});
                    this.state.availableNumbers = [];
                } else {
                    this.state.availableNumbers = res.numbers;
                    this.notification.add(`Found ${res.numbers.length} numbers`, {type: 'success'});
                }
            } else {
                this.state.availableNumbers = [...AVAILABLE_NUMBERS_FALLBACK];
            }
        } catch (e) {
            this.notification.add('Failed to fetch numbers. Using demo data.', {type: 'danger'});
            this.state.availableNumbers = [...AVAILABLE_NUMBERS_FALLBACK];
        } finally {
            this.state.isLoadingNumbers = false;
        }
    }

    goToPage(page) {
        if (page >= 1 && page <= this.totalPages) {
            this.state.currentPage = page;
        }
    }

    buyNumber(number) {
        this.notification.add(`Buying number ${number.number}...`, {type: 'info'});
        setTimeout(() => {
            this.state.myNumbers.push({number: number.number, status: 'Pending'});
            this.notification.add(`Number ${number.number} purchased successfully!`, {type: 'success'});
            this.state.availableNumbers = this.state.availableNumbers.filter(n => n.id !== number.id);
            // Update pagination
            if (this.paginatedNumbers.length === 0 && this.state.currentPage > 1) {
                this.state.currentPage--;
            }
        }, 1500);
    }

    // ========== STRIPE PAYMENT ==========

    openPaymentModal(plan, amount, description) {
        this.state.paymentPlan = plan || "Pro Plan";
        this.state.paymentAmount = amount || "99.00";
        this.state.paymentDescription = description || "WhatsApp Dashboard Subscription";
        this.state.showPaymentModal = true;
        this.state.paymentError = null;
        this.state.isProcessingPayment = false;
        this.state.cardHolderName = "";
        this.state.cardEmail = "";
        this.state.cardCountry = "US";
        this.state.cardZip = "";
    }

    closePaymentModal() {
        this.state.showPaymentModal = false;
        this.state.paymentError = null;
        this.state.isProcessingPayment = false;
    }

    async processPayment() {
        if (this.state.isProcessingPayment) return;

        // Basic validation
        if (!this.state.cardHolderName.trim()) {
            this.state.paymentError = "Please enter the name on card.";
            return;
        }
        if (!this.state.cardEmail.trim() || !this.state.cardEmail.includes('@')) {
            this.state.paymentError = "Please enter a valid email address.";
            return;
        }

        this.state.isProcessingPayment = true;
        this.state.paymentError = null;

        try {
            // 1. Create PaymentIntent on backend
            const intentRes = await this.rpc("/whatsapp_dashboard/stripe/create_payment_intent", {
                amount: this.state.paymentAmount,
                currency: 'usd',
                description: this.state.paymentDescription,
            });

            if (intentRes.error) {
                throw new Error(intentRes.error);
            }

            // 2. Get Stripe publishable key
            const configRes = await this.rpc("/whatsapp_dashboard/stripe/config", {});
            if (!configRes.publishable_key) {
                throw new Error("Stripe publishable key not found");
            }

            // 3. Load Stripe.js if not already loaded
            if (!window.Stripe) {
                await this._loadStripeJS();
            }

            const stripe = window.Stripe(configRes.publishable_key);

            // 4. Confirm the PaymentIntent
            // For demo purposes, we'll simulate a successful payment
            // In production, you'd use stripe.confirmCardPayment with Elements
            const result = await this._simulateStripeConfirm(intentRes.client_secret);

            if (result.error) {
                throw new Error(result.error.message);
            }

            // 5. Payment succeeded
            this.notification.add('Payment successful! Your subscription is now active.', {type: 'success'});
            this.closePaymentModal();

            // Trigger any post-payment actions
            this._handleSuccessfulPayment();

        } catch (error) {
            console.error('Payment error:', error);
            this.state.paymentError = error.message || 'Payment failed. Please try again.';
            this.notification.add('Payment failed: ' + this.state.paymentError, {type: 'danger'});
        } finally {
            this.state.isProcessingPayment = false;
        }
    }

    _loadStripeJS() {
        return new Promise((resolve, reject) => {
            if (document.querySelector('script[src*="stripe.com/v3"]')) {
                resolve();
                return;
            }
            const script = document.createElement('script');
            script.src = 'https://js.stripe.com/v3/';
            script.onload = resolve;
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }

    async _simulateStripeConfirm(clientSecret) {
        // This is a simulation - in production you would use Stripe Elements
        // For demo purposes, we simulate a successful confirmation
        return new Promise((resolve) => {
            setTimeout(() => {
                resolve({
                    paymentIntent: {
                        id: 'pi_' + Date.now(),
                        status: 'succeeded'
                    }
                });
            }, 1500);
        });
    }

    _handleSuccessfulPayment() {
        // Add business logic here - e.g., activate the purchased phone number
        this.notification.add('Your payment has been processed successfully!', {type: 'success'});
    }

    // ========== SUBACCOUNTS ==========

    async loadSubaccounts() {
        try {
            const res = await this.rpc("/whatsapp_dashboard/subaccounts", {});
            if (res && res.subaccounts) {
                this.state.subaccounts = res.subaccounts;
                this.state.totalPhoneNumbers = res.total_phone_numbers || 0;
                this.state.totalSmsSent = res.total_sms_sent || 0;
                this.state.totalVoiceMinutes = res.total_voice_minutes || 0;
            }
        } catch (e) {
            console.error("Failed to load subaccounts:", e);
        }
    }

    formatNumber(num) {
        if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
        return num.toString();
    }

    openCreateModal() {
        this.state.showCreateModal = true;
        this.state.newSubaccount = {
            name: "",
            uniqueName: "",
            email: "",
            status: "active",
            subaccountType: "standard",
            capabilities: { voice: true, sms: true, mms: true, whatsapp: true }
        };
    }

    closeCreateModal() {
        this.state.showCreateModal = false;
    }

    async createSubaccount() {
        const data = this.state.newSubaccount;
        if (this.state.creating) return;

        if (!data.name.trim() || !data.uniqueName.trim()) {
            this.notification.add("Please fill in all required fields.", {type: "danger"});
            return;
        }

        this.state.creating = true;
        try {
            const res = await this.rpc("/whatsapp_dashboard/subaccount/create", {
                name: data.name,
                unique_name: data.uniqueName,
                email: data.email,
                status: data.status,
                subaccount_type: data.subaccountType,
                voice: data.capabilities.voice,
                sms: data.capabilities.sms,
                mms: data.capabilities.mms,
                whatsapp: data.capabilities.whatsapp
            });

            if (res.error) {
                this.notification.add(res.error, { type: "danger" });
                return;
            }

            if (res.success) {
                this.state.subaccounts.unshift(res.subaccount);
                let msg = "Subaccount created successfully!";
                if (res.twilio_sid) msg += " (Synced to Twilio)";
                this.notification.add(msg, { type: "success" });
                this.closeCreateModal();
                this.loadSubaccounts();
            }
        } catch (e) {
            this.notification.add("Failed to create subaccount", { type: "danger" });
        } finally {
            this.state.creating = false;
        }
    }

    openEditModal(subaccount) {
        this.state.editSubaccount = {
            id: subaccount.id,
            name: subaccount.name,
            uniqueName: subaccount.unique_name,
            email: subaccount.email,
            status: subaccount.status
        };
        this.state.showEditModal = true;
    }

    closeEditModal() {
        this.state.showEditModal = false;
    }

    async updateSubaccount() {
        const data = this.state.editSubaccount;
        if (!data.name.trim() || !data.uniqueName.trim()) {
            this.notification.add("Please fill in all required fields.", {type: "danger"});
            return;
        }

        try {
            const res = await this.rpc("/whatsapp_dashboard/subaccount/update", {
                subaccount_id: data.id,
                name: data.name,
                unique_name: data.uniqueName,
                email: data.email,
                status: data.status
            });

            if (res.error) {
                this.notification.add(res.error, { type: "danger" });
                return;
            }

            if (res.success) {
                const idx = this.state.subaccounts.findIndex(s => s.id === data.id);
                if (idx !== -1) this.state.subaccounts[idx] = res.subaccount;
                this.notification.add("Subaccount updated!", { type: "success" });
                this.closeEditModal();
                this.loadSubaccounts();
            }
        } catch (e) {
            this.notification.add("Failed to update subaccount", { type: "danger" });
        }
    }

    async confirmDelete(subaccount) {
        if (!confirm(`Delete subaccount "${subaccount.name}"?\n\nThis will also close it permanently on Twilio.`)) return;
        try {
            const res = await this.rpc("/whatsapp_dashboard/subaccount/delete", {subaccount_id: subaccount.id});
            if (res.success) {
                this.state.subaccounts = this.state.subaccounts.filter(s => s.id !== subaccount.id);
                this.notification.add(res.message, { type: "success" });
                this.loadSubaccounts();
            }
        } catch (e) {
            this.notification.add("Failed to delete subaccount", { type: "danger" });
        }
    }

    async toggleSubaccountStatus(subaccount) {
        try {
            const res = await this.rpc("/whatsapp_dashboard/subaccount/toggle_status", { subaccount_id: subaccount.id });
            if (res.success) {
                const idx = this.state.subaccounts.findIndex(s => s.id === subaccount.id);
                if (idx >= 0) this.state.subaccounts[idx] = res.subaccount;
                if (res.warning) {
                    this.notification.add(res.warning, { type: "warning" });
                } else {
                    this.notification.add(`Subaccount ${res.status}`, { type: "success" });
                }
                this.loadSubaccounts();
            }
            if (res.error) this.notification.add(res.error, { type: "danger" });
        } catch (e) {
            this.notification.add("Failed to toggle subaccount status", { type: "danger" });
        }
    }

    openDetailPanel(subaccount) {
        this.state.selectedSubaccount = subaccount;
        this.state.showDetailPanel = true;
    }

    closeDetailPanel() {
        this.state.showDetailPanel = false;
        this.state.selectedSubaccount = null;
    }

    toggleCapability(cap) {
        this.state.newSubaccount.capabilities[cap] = !this.state.newSubaccount.capabilities[cap];
    }
}

registry.category("actions").add("whatsapp_dashboard", WhatsAppDashboard);