import { createApp } from "vue"
import { createPinia } from "pinia"
import App from "./App.vue"
import router from "./router"
import { scrollHint } from "./directives/scrollHint"
import { installTooltip } from "./utils/tooltip"
import "./styles/index.css"

createApp(App).use(createPinia()).use(router).directive("scroll-hint", scrollHint).mount("#app")

// 全站 tooltip：接管原生 title，换成 WorkBuddy 样式的深灰气泡（§6.24）
installTooltip()
