import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('@/views/Dashboard.vue'),
    },
    {
      path: '/screener',
      name: 'screener',
      component: () => import('@/views/Screener.vue'),
    },
    {
      path: '/stock/:code',
      name: 'stock-detail',
      component: () => import('@/views/StockDetail.vue'),
    },
    {
      path: '/ml-signals',
      name: 'ml-signals',
      component: () => import('@/views/MLSignals.vue'),
    },
  ],
})

export default router
