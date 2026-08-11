// 보호 화면의 본문이 그려지기 전에 실행한다. 로그인 화면을 거치지 않은 직접 접근과
// 다른 계정의 로컬 사본이 섞이는 일을 먼저 막는다.
(function () {
  "use strict";
  var current = window.AccountStore && AccountStore.auth();
  var userId = current && current.user && current.user.id;
  var owner = localStorage.getItem(AccountStore.OWNER_KEY);
  if (!userId || owner !== userId) {
    if (owner !== userId) {
      localStorage.removeItem("assetInput.session");
      localStorage.removeItem(AccountStore.OWNER_KEY);
    }
    var here = location.pathname.split("/").pop() + location.search + location.hash;
    location.replace("login.html?return=" + encodeURIComponent(here));
  } else {
    AccountStore.bootstrapProtected();
  }
})();
