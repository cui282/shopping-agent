export interface StarterQuery {
  title: string;
  query: string;
  image: string;
  imageAlt: string;
}

export const starterQueries: StarterQuery[] = [
  {
    title: "通勤降噪耳机",
    query: "预算 1200 元，找一款轻便降噪耳机，不要皮革，适合每天通勤",
    image: "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=640&q=82",
    imageAlt: "黑色头戴式耳机",
  },
  {
    title: "手冲咖啡磨豆机",
    query: "对比 800 元以内的手摇磨豆机，重点看研磨均匀度、重量和清洁难度",
    image: "https://images.unsplash.com/photo-1517668808822-9ebb02f2a0e6?auto=format&fit=crop&w=640&q=82",
    imageAlt: "咖啡器具与咖啡豆",
  },
  {
    title: "轻量徒步背包",
    query: "找 20L 左右的轻量徒步背包，预算 600 元，需要防泼水和腰带",
    image: "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?auto=format&fit=crop&w=640&q=82",
    imageAlt: "户外背包",
  },
];
