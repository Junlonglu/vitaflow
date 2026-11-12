import { ref } from "vue"

/**
 * 工作区侧栏抽屉的开合状态。
 *
 * 桌面端侧栏常驻、这个值不参与布局；移动端（<768px）侧栏变成从左侧滑出的抽屉，
 * 由工作区首页与编辑器顶栏的菜单按钮控制。模块级单例，Shell 和触发按钮共用同一份状态。
 */
const drawerOpen = ref(false)

export function useWorkspaceDrawer() {
  return {
    drawerOpen,
    openDrawer: () => {
      drawerOpen.value = true
    },
    closeDrawer: () => {
      drawerOpen.value = false
    },
    toggleDrawer: () => {
      drawerOpen.value = !drawerOpen.value
    },
  }
}
