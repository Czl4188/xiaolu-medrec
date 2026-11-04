const { apiBase, shareCode } = require('./config');

const TOKEN_KEY = 'lx_token';
const USER_KEY = 'lx_user';

function readToken() {
    return wx.getStorageSync(TOKEN_KEY) || '';
}

function readUser() {
    return wx.getStorageSync(USER_KEY) || null;
}

function saveSession(token, user) {
    wx.setStorageSync(TOKEN_KEY, token);
    wx.setStorageSync(USER_KEY, user || null);
}

function clearSession() {
    wx.removeStorageSync(TOKEN_KEY);
    wx.removeStorageSync(USER_KEY);
}

function withShare(path) {
    if (!shareCode) {
        return path;
    }
    const sep = path.indexOf('?') >= 0 ? '&' : '?';
    return path + sep + 'sharecode=' + shareCode;
}

function pickMessage(status, data) {
    const detail = data && data.detail;
    if (typeof detail === 'string' && detail) {
        return detail;
    }
    if (Array.isArray(detail) && detail.length && detail[0] && detail[0].msg) {
        return detail[0].msg;
    }
    return '请求失败（' + status + '）';
}

function request(path, options) {
    const opts = options || {};
    return new Promise(function (resolve, reject) {
        const header = { 'Content-Type': 'application/json' };
        const token = readToken();
        if (token) {
            header['X-Xiaolu-Token'] = token;
        }
        wx.request({
            url: apiBase + withShare(path),
            method: opts.method || 'GET',
            data: opts.data,
            header: header,
            timeout: opts.timeout || 30000,
            success: function (res) {
                const status = res.statusCode;
                if (status >= 200 && status < 300) {
                    resolve(res.data);
                    return;
                }
                const err = new Error(pickMessage(status, res.data));
                err.status = status;
                reject(err);
            },
            fail: function (e) {
                reject(new Error((e && e.errMsg) || '网络异常，检查一下网络'));
            },
        });
    });
}

function login(phone, password) {
    return request('/api/auth/login', {
        method: 'POST',
        data: { phone: phone, password: password },
    }).then(function (r) {
        saveSession(r.access_token, r.user);
        return r.user;
    });
}

function register(username, phone, password) {
    return request('/api/auth/register', {
        method: 'POST',
        data: { username: username, phone: phone, password: password },
    }).then(function (r) {
        saveSession(r.access_token, r.user);
        return r.user;
    });
}

function logout() {
    clearSession();
}

module.exports = {
    request: request,
    login: login,
    register: register,
    logout: logout,
    readToken: readToken,
    readUser: readUser,
    saveSession: saveSession,
    clearSession: clearSession,
};
