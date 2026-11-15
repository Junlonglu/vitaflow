import { createApp } from "vue"
import { createPinia } from "pinia"
import App from "./App.vue"
import router from "./router"
import { scrollHint } from "./directives/scrollHint"
import "./styles/index.css"

createApp(App).use(createPinia()).use(router).directive("scroll-hint", scrollHint).mount("#app")
