import React from 'react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { RefreshCw } from 'lucide-react';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  isLoading?: boolean;
  icon?: React.ReactNode;
}

export const Button: React.FC<ButtonProps> = ({
  children,
  variant = 'primary',
  size = 'md',
  isLoading,
  icon,
  className,
  disabled,
  ...props
}) => {
  const variants = {
    primary:
      'bg-primary text-white shadow-lg shadow-primary/20 hover:bg-blue-600 uiv2:bg-v2-accent uiv2:text-v2-bg uiv2:shadow-none uiv2:hover:bg-[#d9ff62] uiv2:hover:shadow-[0_0_16px_0_rgba(198,242,74,0.45)] uiv3:bg-v3-accent uiv3:text-[#1a0b07] uiv3:shadow-none uiv3:hover:bg-v3-accent-bright',
    secondary:
      'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 uiv2:border uiv2:border-v2-border uiv2:bg-v2-surface-muted uiv2:text-v2-text uiv2:hover:border-v2-border-bright uiv2:hover:bg-v2-surface uiv3:border uiv3:border-v3-border uiv3:bg-v3-surface-muted uiv3:text-v3-text uiv3:hover:border-v3-border-bright uiv3:hover:bg-v3-surface',
    outline:
      'border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 uiv2:border-v2-border uiv2:text-v2-text-muted uiv2:hover:border-v2-accent uiv2:hover:bg-v2-accent-soft uiv2:hover:text-v2-accent uiv3:border-v3-border uiv3:text-v3-text-muted uiv3:hover:border-v3-accent uiv3:hover:bg-v3-accent-soft uiv3:hover:text-v3-accent-bright',
    ghost:
      'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 uiv2:text-v2-text-muted uiv2:hover:bg-v2-accent-soft uiv2:hover:text-v2-accent uiv3:text-v3-text-muted uiv3:hover:bg-v3-accent-soft uiv3:hover:text-v3-accent-bright',
    danger:
      'bg-red-50 text-red-600 hover:bg-red-100 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/30 uiv2:border uiv2:border-[#5c2b2b] uiv2:bg-[rgba(248,113,113,0.08)] uiv2:text-[#f87171] uiv2:hover:bg-[rgba(248,113,113,0.16)] uiv3:border uiv3:border-[#a33a3a] uiv3:bg-[rgba(230,90,90,0.12)] uiv3:text-[#f0938c] uiv3:hover:bg-[rgba(230,90,90,0.22)]',
  };

  const sizes = {
    sm: 'px-3 py-1.5 text-xs font-bold rounded-lg',
    md: 'px-4 py-2 text-sm font-bold rounded-xl',
    lg: 'px-6 py-3 text-base font-bold rounded-xl',
  };

  return (
    <button
      className={cn(
        'inline-flex items-center justify-center gap-2 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed',
        'uiv2:font-terminal uiv2:uppercase uiv2:tracking-[0.12em]',
        'uiv3:font-typewriter uiv3:uppercase uiv3:tracking-[0.1em]',
        variants[variant],
        sizes[size],
        className
      )}
      disabled={disabled || isLoading}
      {...props}
    >
      {isLoading ? <RefreshCw size={16} className="animate-spin" /> : icon}
      {children}
    </button>
  );
};
