const os = require('os');
const real = os.userInfo;
os.userInfo = function () {
  try { return real.call(os); } catch (e) {
    return { uid: 1000, gid: 1000, username: 'builder', homedir: process.env.HOME || '/workspace', shell: '/bin/sh' };
  }
};
