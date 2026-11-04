const { request, readToken } = require('../../utils/api');

Page({
    data: {
        logged: true,
        messages: [],
        input: '',
        busy: false,
        intoView: '',
        chips: ['我上次看病是什么时候？', '我开的什么药？', '什么时候复诊？'],
    },

    onShow() {
        const logged = !!readToken();
        if (this.data.logged !== logged) {
            this.setData({ logged: logged, messages: logged ? this.data.messages : [] });
        }
        if (logged && this.data.messages.length === 0) {
            this.appendBot('我是小鹿。只按你的档案回答，档案里没有的我会直说。');
        }
    },

    appendBot(text) {
        const list = this.data.messages.concat([{ id: 'm' + this.data.messages.length, role: 'bot', text: text }]);
        this.setData({ messages: list, intoView: list[list.length - 1].id });
    },

    appendUser(text) {
        const list = this.data.messages.concat([{ id: 'm' + this.data.messages.length, role: 'user', text: text }]);
        this.setData({ messages: list, intoView: list[list.length - 1].id });
    },

    onInput(e) {
        this.setData({ input: e.detail.value });
    },

    tapChip(e) {
        const text = e.currentTarget.dataset.text;
        this.setData({ input: text });
        this.send();
    },

    send() {
        const question = (this.data.input || '').trim();
        if (!question || this.data.busy) {
            return;
        }
        if (!readToken()) {
            this.setData({ logged: false });
            return;
        }
        this.appendUser(question);
        this.setData({ input: '', busy: true });
        request('/api/chat/sync', {
            method: 'POST',
            data: { question: question },
            timeout: 60000,
        })
            .then((r) => {
                let text = (r && r.answer) || '这次没有拿到答案。';
                const cites = (r && r.citations) || [];
                if (cites.length) {
                    text += '\n\n来源：' + cites.map(function (c) {
                        return (c.visit_at || '').slice(0, 10) + ' ' + (c.hospital || '');
                    }).join('；');
                }
                this.appendBot(text);
            })
            .catch((err) => {
                this.appendBot('没能回答：' + (err.message || '网络异常'));
            })
            .then(() => {
                this.setData({ busy: false });
            });
    },

    goLogin() {
        wx.navigateTo({ url: '/pages/login/login' });
    },
});
